"""Exercise public exports and submission attacks without making model requests."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from agent import freeze, prepare_workspace, safe_name, task_instructions, task_list, write_public
from evaluate import (
    CVDPTask,
    JudgeSettings,
    Status,
    comprehension_evaluate,
    cvdp_evaluate,
    execute,
    prepare_cvdp_code,
)
from fetch import LFSFile, download
from sglang_proxy import Sampling, configure_request


class FetchIntegrityTests(unittest.TestCase):
    def test_truncated_bundle_is_quarantined_and_partial_transfer_resumes(self) -> None:
        payload = b"validated bundle bytes" * 20
        expected = LFSFile(len(payload), hashlib.sha256(payload).hexdigest())

        class Response(io.BytesIO):
            def __init__(self, data: bytes, status: int, headers: dict[str, str]):
                super().__init__(data)
                self.status = status
                self.headers = headers

        responses = [
            Response(payload[:37], 200, {"Content-Length": str(len(payload))}),
            Response(
                payload[37:],
                206,
                {
                    "Content-Length": str(len(payload) - 37),
                    "Content-Range": f"bytes 37-{len(payload) - 1}/{len(payload)}",
                },
            ),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "data/cvdp/context.bundle"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"old truncated bundle")
            with (
                mock.patch("fetch.ROOT", root),
                mock.patch("fetch.lfs_file", return_value=expected),
                mock.patch("fetch.urllib.request.urlopen", side_effect=responses) as opener,
            ):
                download("context.bundle")
            self.assertEqual(target.read_bytes(), payload)
            self.assertEqual(opener.call_args_list[1].args[0].get_header("Range"), "bytes=37-")
            quarantined = list((root / "cache/invalid-bundles").iterdir())
            self.assertEqual(len(quarantined), 1)
            self.assertEqual(quarantined[0].read_bytes(), b"old truncated bundle")

    def test_wrong_bundle_hash_is_never_promoted(self) -> None:
        expected = LFSFile(8, hashlib.sha256(b"expected").hexdigest())
        responses = [mock.MagicMock() for _ in range(3)]
        for response in responses:
            stream = io.BytesIO(b"badbytes")
            stream.status = 200
            stream.headers = {"Content-Length": "8"}
            response.__enter__.return_value = stream
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                mock.patch("fetch.ROOT", root),
                mock.patch("fetch.lfs_file", return_value=expected),
                mock.patch("fetch.urllib.request.urlopen", side_effect=responses),
                self.assertRaises(OSError),
            ):
                download("context.bundle")
            self.assertFalse((root / "data/cvdp/context.bundle").exists())


class AgentBoundaryTests(unittest.TestCase):
    def test_golden_failures_are_filtered_per_task_format_and_auditable(self) -> None:
        self.assertEqual(len(task_list("rtllm", None, "spec-to-rtl")), 47)
        self.assertEqual(len(task_list("rtllm", None, "spec-to-rtl", include_excluded=True)), 50)
        self.assertEqual(len(task_list("verilogeval", None, "spec-to-rtl")), 155)
        self.assertEqual(len(task_list("verilogeval", None, "code-complete-iccad2023")), 156)
        with self.assertRaisesRegex(ValueError, "Golden-failing task excluded"):
            task_list("rtllm", ["ring_counter"], "spec-to-rtl")

    def test_only_null_python_cache_placeholders_are_ignored(self) -> None:
        row = {
            "id": "cache-placeholder",
            "categories": [],
            "input": {"context": {}},
            "output": {"context": {"rtl/dut.sv": ""}},
            "harness": {"files": {"src/__pycache__/runner.pyc": None, "src/test.py": "test"}},
        }
        task = CVDPTask.decode(row)
        self.assertEqual(task.harness, {"src/test.py": "test"})
        self.assertEqual(task.ignored_harness_files, ("src/__pycache__/runner.pyc",))
        row["harness"]["files"]["src/test.py"] = None
        with self.assertRaisesRegex(ValueError, "Non-text required harness file"):
            CVDPTask.decode(row)

    def test_heavy_compose_external_alias_uses_frozen_candidate_tree(self) -> None:
        task = CVDPTask(
            "external-alias",
            [],
            {},
            {"rtl/dut.sv": "module dut; endmodule"},
            False,
            {
                "docker-compose.yml": (
                    "services:\n  test:\n    volumes: ['../../external/:/code/external/:ro']\n"
                    "    command: ['true']\n"
                )
            },
            "bundle-repo",
            "a" * 40,
            "external",
        )

        def on_host(image, binds, cwd, command, env):
            source = next(bind.source for bind in binds if bind.target == "/code/external/")
            return (
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; "
                    "assert 'endmodule' in Path('rtl/dut.sv').read_text()",
                ]
                if source.is_dir()
                else ["false"]
            )

        def prepare(official, directory):
            directory.mkdir()

        with tempfile.TemporaryDirectory() as temporary:
            with (
                mock.patch("evaluate.prepare_cvdp_code", prepare),
                mock.patch("evaluate.container_command", on_host),
            ):
                # A real host child checks the mapped candidate content.
                def execute_mapped(command, cwd, log, timeout):
                    return execute(command, cwd / "code", log, timeout)

                with mock.patch("evaluate.execute", execute_mapped):
                    result = cvdp_evaluate(task, Path(temporary), 5, False)
            self.assertEqual(result.status, Status.PASS)

    def test_invalid_service_mount_is_judge_error_not_agent_failure(self) -> None:
        task = CVDPTask(
            "invalid-mount",
            [],
            {},
            {"rtl/dut.sv": "module dut; endmodule"},
            False,
            {
                "docker-compose.yml": (
                    "services:\n  test:\n    volumes: ['../../secret:/secret']\n"
                    "    command: ['true']\n"
                )
            },
            None,
            None,
            None,
        )
        with tempfile.TemporaryDirectory() as temporary:
            result = cvdp_evaluate(task, Path(temporary), 5, False)
        self.assertEqual(result.status, Status.ERROR)
        self.assertIn("volume escapes harness", result.detail)
        self.assertEqual(result.executions, [])

    def test_all_cvdp_subsets_are_selectable_without_solutions(self) -> None:
        expected = {"nonagentic": 302, "agentic": 92, "heavy": 75, "comprehension": 123}
        identifiers = set()
        for split, count in expected.items():
            tasks = task_list("cvdp", None, "spec-to-rtl", split)
            self.assertEqual(len(tasks), count)
            identifiers.update(task.identifier for task in tasks)
            for task in tasks:
                self.assertTrue(task.prompt)
                self.assertTrue(task.outputs)
                self.assertNotIn("docker-compose.yml", task.context)
        self.assertEqual(len(identifiers), 592)
        self.assertEqual(len(task_list("cvdp", None, "spec-to-rtl", "all")), 592)

    def test_comprehension_exports_input_and_collects_only_response(self) -> None:
        task = task_list("cvdp", None, "spec-to-rtl", "comprehension")[0]
        official = replace(
            task.cvdp, reference="PRIVATE_ANSWER", harness={"hidden-test": "PRIVATE_TEST"}
        )
        task = replace(task, cvdp=official)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            self.assertIsNone(prepare_workspace(task, workspace))
            self.assertEqual(
                {
                    str(file.relative_to(workspace))
                    for file in workspace.rglob("*")
                    if file.is_file()
                },
                set(task.context) | {"problem.txt"},
            )
            for file in workspace.rglob("*"):
                if file.is_file():
                    self.assertNotIn("PRIVATE_", file.read_text())
            self.assertNotIn("PRIVATE_", task_instructions(task))
            (workspace / "subjective.txt").write_text("My own analysis")
            (workspace / "selftest.sv").write_text("not an answer")
            records = freeze(workspace, root / "frozen", task.outputs)
            self.assertEqual([record.path for record in records], ["subjective.txt"])

    def test_patch_freeze_preserves_new_files_markdown_and_deletions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            write_public(
                workspace, {"rtl/new.sv": "module dut; endmodule", "docs/README.md": "Updated\n"}
            )
            originals = {"rtl/deleted.sv": "old\n", "docs/README.md": "Original\n"}
            outputs = ("rtl/new.sv", "docs/README.md", "rtl/deleted.sv")
            records = freeze(workspace, root / "frozen", outputs, originals)
            self.assertTrue(records[2].deleted)
            self.assertEqual((root / "frozen/docs/README.md").read_text(), "Updated\n")
            restored = root / "restored"
            restored.mkdir()
            write_public(restored, originals)
            subprocess.run(
                ["patch", "-p1", "-i", str(root / "frozen/changes.patch")],
                cwd=restored,
                check=True,
                capture_output=True,
            )
            self.assertEqual((restored / "rtl/new.sv").read_text(), "module dut; endmodule")
            self.assertFalse((restored / "rtl/deleted.sv").exists())
            self.assertEqual((restored / "docs/README.md").read_text(), "Updated\n")

    def test_heavy_export_cannot_reveal_future_commits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            repo.mkdir()

            def git(*args: str) -> str:
                return (
                    subprocess.check_output(
                        ["git", "-C", str(repo), *args], stderr=subprocess.DEVNULL
                    )
                    .decode()
                    .strip()
                )

            git("init")
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.invalid")
            (repo / "external").mkdir()
            (repo / "external/dut.sv").write_text("public baseline")
            (repo / "external/.gitattributes").write_text("dut.sv export-ignore\n")
            (repo / "private_harness.py").write_text("Wrapper scaffolding must not be exported")
            git("add", ".")
            git("commit", "-m", "baseline")
            commit = git("rev-parse", "HEAD")
            (repo / "secret-golden.txt").write_text("future solution")
            git("add", ".")
            git("commit", "-m", "future answer")
            bundles = root / "data/cvdp/cvdp_v1.1.0_agentic_heavy_code_generation_public"
            bundles.mkdir(parents=True)
            git("bundle", "create", str(bundles / "cvdp_example.bundle"), "--all")
            task = CVDPTask.decode(
                {
                    "id": "export",
                    "categories": ["cid016"],
                    "prompt": "Implement dut",
                    "context": {"repo": "cvdp_example", "commit": commit},
                    "patch": {"dut.sv": ""},
                    "harness": {},
                }
            )
            with mock.patch("evaluate.ROOT", root):
                prepare_cvdp_code(task, root / "public")
            self.assertEqual(
                sorted(file.name for file in (root / "public").iterdir()),
                [".gitattributes", "dut.sv"],
            )
            self.assertEqual((root / "public/dut.sv").read_text(), "public baseline")

    def test_patch_judge_applies_frozen_files_without_gold_patch(self) -> None:
        task = CVDPTask(
            "patch-submission",
            [],
            {"rtl/dut.sv": "broken", "docs/old.md": "remove me"},
            {"rtl/dut.sv": "", "docs/old.md": "", "docs/new.md": ""},
            True,
            {"docker-compose.yml": "services:\n  test:\n    command: ['true']\n"},
            None,
            None,
            None,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            write_public(
                workspace, {"rtl/dut.sv": "module dut; endmodule", "docs/new.md": "new docs"}
            )
            freeze(workspace, root / "submission", tuple(task.golden), task.context)
            out = root / "judge"
            out.mkdir()
            with mock.patch("evaluate.container_command", return_value=["true"]):
                result = cvdp_evaluate(task, out, 5, False, root / "submission")
            self.assertEqual(result.status, Status.PASS)
            code = out / task.identifier / "code"
            self.assertEqual((code / "rtl/dut.sv").read_text(), "module dut; endmodule")
            self.assertFalse((code / "docs/old.md").exists())
            self.assertEqual((code / "docs/new.md").read_text(), "new docs")

    def test_public_exports_exclude_official_tests_and_solutions(self) -> None:
        selections = (
            ("rtllm", "adder_8bit"),
            ("verilogeval", "Prob009_popcount3"),
            ("cvdp", "cvdp_copilot_bcd_adder_0001"),
        )
        for bench, identifier in selections:
            with self.subTest(benchmark=bench), tempfile.TemporaryDirectory() as temporary:
                task = task_list(bench, [identifier], "spec-to-rtl")[0]
                workspace = Path(temporary)
                write_public(workspace, {"problem.txt": task.prompt, **task.context})
                files = sorted(str(file.relative_to(workspace)) for file in workspace.rglob("*"))
                self.assertEqual(files, ["problem.txt"])
                self.assertTrue((workspace / "problem.txt").read_text())

    def test_submission_is_frozen_and_hashed_independently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "candidate.sv").write_text("module dut; endmodule\n")
            records = freeze(workspace, root / "frozen", ("candidate.sv",))
            (workspace / "candidate.sv").write_text("changed after freeze")
            self.assertEqual((root / "frozen/candidate.sv").read_text(), "module dut; endmodule\n")
            self.assertEqual(len(records[0].sha256), 64)
            self.assertEqual((root / "frozen/candidate.sv").stat().st_mode & 0o222, 0)

    def test_leaf_and_parent_symlinks_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "secret.sv").write_text("must not be collected")
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "leaf.sv").symlink_to(root / "secret.sv")
            (workspace / "escape").symlink_to(root)
            for index, name in enumerate(("leaf.sv", "escape/secret.sv")):
                with self.subTest(path=name), self.assertRaisesRegex(ValueError, "Symlink"):
                    freeze(workspace, root / f"frozen{index}", (name,))

    def test_fifo_is_rejected_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            os.mkfifo(workspace / "candidate.sv")
            with self.assertRaisesRegex(ValueError, "Invalid RTL"):
                freeze(workspace, root / "frozen", ("candidate.sv",))

    def test_timeout_kills_simulator_descendants(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / "escaped-child"
            child = "import time; from pathlib import Path; time.sleep(2); " + (
                f"Path({str(marker)!r}).write_text('still running')"
            )
            parent = (
                "import subprocess, sys, time; "
                f"subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(60)"
            )
            result = execute([sys.executable, "-c", parent], root, root / "process.log", 1)
            self.assertTrue(result.timed_out)
            time.sleep(2)
            self.assertFalse(marker.exists())

    def test_controller_interruption_terminates_running_container_process(self) -> None:
        processes = []
        original_wait = subprocess.Popen.wait

        def interrupted(process: subprocess.Popen, timeout: float | None = None) -> int:
            if not processes:
                processes.append(process)
                raise KeyboardInterrupt()
            return original_wait(process, timeout)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                mock.patch.object(subprocess.Popen, "wait", interrupted),
                self.assertRaises(KeyboardInterrupt),
            ):
                execute(
                    [sys.executable, "-c", "import time; time.sleep(60)"],
                    root,
                    root / "process.log",
                    60,
                )
            self.assertIsNotNone(processes[0].poll())

    def test_sglang_gateway_rejects_web_search_and_fetch(self) -> None:
        for tool in (
            {"type": "web_search"},
            {"type": "function", "name": "web_fetch"},
            {"type": "function", "name": "fetch"},
        ):
            with self.subTest(tool=tool), self.assertRaisesRegex(ValueError, "Nonlocal tool"):
                configure_request({"model": "qwen", "tools": [tool]}, Sampling("qwen"))

    def test_sampling_cannot_silently_disable_thinking_or_change_conversation(self) -> None:
        conversation = [{"role": "user", "content": "Implement an adder"}]
        original = {
            "model": "qwen",
            "input": conversation,
            "top_p": 0.1,
            "chat_template_kwargs": {"enable_thinking": False},
            "tools": [{"type": "function", "name": "exec_command"}],
        }
        effective = configure_request(original, Sampling("qwen"))
        self.assertEqual(effective["input"], conversation)
        self.assertTrue(effective["chat_template_kwargs"]["enable_thinking"])
        self.assertEqual(effective["top_p"], 0.95)
        self.assertNotIn("thinking_budget", effective["chat_template_kwargs"])
        self.assertFalse(original["chat_template_kwargs"]["enable_thinking"])

    def test_public_paths_cannot_escape_workspace(self) -> None:
        for name in ("../hidden", "/tmp/hidden", "rtl/../../hidden"):
            with self.subTest(path=name), self.assertRaises(ValueError):
                safe_name(name)

    def test_cvdp_prepares_writable_cwd_inside_each_task_mount(self) -> None:
        # Run a real child process through the judge; replace only Apptainer's
        # path translation so this regression also runs without a local SIF.
        def on_host(image, binds, cwd, command, env):
            source = next(bind.source for bind in binds if bind.target.rstrip("/") == "/src")
            directory = source / "rundir"
            return ["/bin/sh", "-c", 'cd "$1" && touch lint_results.log', "sh", str(directory)]

        task = CVDPTask(
            "cwd-regression",
            [],
            {},
            {"rtl/dut.sv": "module dut; endmodule"},
            False,
            {
                "docker-compose.yml": (
                    "services:\n  lint:\n    working_dir: /src/rundir/\n"
                    "    volumes: ['./src:/src/']\n    command: ['true']\n"
                ),
                "src/lint.py": "",
            },
            None,
            None,
            None,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("first", "second"):
                out = root / name
                out.mkdir()
                with mock.patch("evaluate.container_command", on_host):
                    result = cvdp_evaluate(task, out, 5, False)
                self.assertEqual(result.status, Status.PASS)
                marker = out / task.identifier / "harness/src/rundir/lint_results.log"
                self.assertTrue(marker.is_file())

    def test_cvdp_unmapped_cwd_is_an_environment_error(self) -> None:
        task = CVDPTask(
            "unmapped-cwd",
            [],
            {},
            {"rtl/dut.sv": "module dut; endmodule"},
            False,
            {
                "docker-compose.yml": (
                    "services:\n  lint:\n    working_dir: /unmounted/rundir\n"
                    "    command: ['true']\n"
                )
            },
            None,
            None,
            None,
        )
        with tempfile.TemporaryDirectory() as temporary:
            result = cvdp_evaluate(task, Path(temporary), 5, False)
        self.assertEqual(result.status, Status.ERROR)
        self.assertEqual(result.executions, [])

    def test_cvdp_distinguishes_missing_dependency_from_test_failure(self) -> None:
        task = CVDPTask(
            "failed-service",
            [],
            {},
            {"rtl/dut.sv": "module dut; endmodule"},
            False,
            {"docker-compose.yml": "services:\n  test:\n    command: ['true']\n"},
            None,
            None,
            None,
        )
        cases = (
            ("ModuleNotFoundError: No module named 'cocotb'\n", Status.ERROR),
            ("AssertionError: expected 3, got 2\n", Status.FAIL),
        )
        for message, expected in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temporary:
                command = [
                    sys.executable,
                    "-c",
                    f"import sys; sys.stderr.write({message!r}); sys.exit(1)",
                ]
                with mock.patch("evaluate.container_command", return_value=command):
                    result = cvdp_evaluate(task, Path(temporary), 5, False)
                self.assertEqual(result.status, expected)


@unittest.skipUnless(
    os.environ.get("VERILOG_TEST_SIF") and shutil.which("apptainer"),
    "Set VERILOG_TEST_SIF to run private scoring integration in a CPU allocation",
)
class CVDPScoringTests(unittest.TestCase):
    def test_boolean_parameter_and_sva_keep_real_compilation_and_assertions(self) -> None:
        runner = (
            "import os\nfrom cocotb_tools.runner import get_runner\n"
            "def test_run():\n"
            "    runner = get_runner(os.environ['SIM'])\n"
            "    runner.build(sources=['/code/rtl/dut.sv'], hdl_toplevel='dut', "
            "parameters=PARAMETERS, always=True)\n"
            "    runner.test(hdl_toplevel='dut', test_module='test_dut')\n"
        )
        test = (
            "import cocotb\nfrom cocotb.triggers import Timer\n"
            "@cocotb.test()\nasync def smoke(dut):\n"
            "    dut.clk.value = 0\n"
            "    for _ in range(3):\n"
            "        await Timer(5, unit='ns')\n"
            "        dut.clk.value = 1\n"
            "        await Timer(5, unit='ns')\n"
            "        dut.clk.value = 0\n"
            "    assert int(dut.enabled.value) == 0\n"
        )
        cases = (
            ("bool-parameter", "", "{'KEEP_ENABLE': False}", Status.PASS),
            (
                "cvdp_agentic_heavy_cv32e40p_1_0001",
                "property p; @(posedge clk) 1'b1; endproperty\nassert property(p);\n",
                "{}",
                Status.PASS,
            ),
            (
                "cvdp_agentic_heavy_cv32e40p_1_0001",
                "property p; @(posedge clk) 1'b0; endproperty\nassert property(p);\n",
                "{}",
                Status.FAIL,
            ),
        )
        for identifier, assertion, parameters, expected in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temporary:
                task = CVDPTask(
                    identifier,
                    [],
                    {},
                    {
                        "rtl/dut.sv": (
                        "`timescale 1ns/1ps\nmodule dut #(parameter KEEP_ENABLE=0) "
                            "(input logic clk, output logic enabled);\n"
                            "assign enabled = KEEP_ENABLE;\n" + assertion + "endmodule\n"
                        )
                    },
                    False,
                    {
                        "docker-compose.yml": (
                            "services:\n  test:\n    volumes: ['./src:/src:ro']\n"
                            "    env_file: './src/.env'\n"
                            "    command: ['pytest', '-s', '/src/test_runner.py']\n"
                        ),
                        "src/.env": "SIM=icarus\nPYTHONPATH=/src\n",
                        "src/test_runner.py": runner.replace("PARAMETERS", parameters),
                        "src/test_dut.py": test,
                    },
                    None,
                    None,
                    None,
                )
                result = cvdp_evaluate(
                    task, Path(temporary), 120, False, image=Path(os.environ["VERILOG_TEST_SIF"])
                )
                diagnostics = "\n".join(Path(item.log).read_text() for item in result.executions)
                self.assertEqual(result.status, expected, diagnostics[-4000:])
                if expected == Status.FAIL:
                    self.assertIn("Assertion failed", diagnostics)

    def test_real_objective_metrics_and_missing_subjective_judge(self) -> None:
        reference = "the clocked module increments a counter on each rising clock edge"
        cases = (
            ("cid006", reference, Status.SCORED, 1.0),
            ("cid008", "unrelated words with absolutely zero overlap", Status.SCORED, 0.0),
            ("cid009", "My independent analysis", Status.UNSCORED, None),
            ("cid010", "", Status.FAIL, None),
        )
        image = Path(os.environ["VERILOG_TEST_SIF"]).resolve()
        for category, answer, status, value in cases:
            with self.subTest(category=category), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                candidate = root / "candidate"
                candidate.mkdir()
                (candidate / "subjective.txt").write_text(answer)
                task = CVDPTask(
                    "score",
                    [category],
                    {},
                    {},
                    False,
                    {},
                    None,
                    None,
                    None,
                    "Explain the counter",
                    reference,
                )
                result = comprehension_evaluate(task, root, candidate, 30, image=image)
                self.assertEqual(result.status, status, result)
                if value is not None:
                    self.assertAlmostEqual(result.score.value, value)
                else:
                    self.assertIsNone(result.score)

    def test_official_llm_rubric_uses_private_reference_and_rejects_parse_errors(self) -> None:
        requests = []
        replies = iter(
            (json.dumps({"score": 0.85, "reasoning": "Transport smoke only"}), "not a score")
        )

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                body = json.dumps(
                    {"choices": [{"message": {"role": "assistant", "content": next(replies)}}]}
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                pass

        image = Path(os.environ["VERILOG_TEST_SIF"]).resolve()
        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            settings = JudgeSettings(
                "transport-smoke", f"http://127.0.0.1:{server.server_port}/v1", "CVDP_TEST_API_KEY"
            )
            try:
                for expected in (Status.SCORED, Status.ERROR):
                    with (
                        self.subTest(expected=expected),
                        tempfile.TemporaryDirectory() as temporary,
                    ):
                        root = Path(temporary)
                        candidate = root / "candidate"
                        candidate.mkdir()
                        (candidate / "subjective.txt").write_text("Independent candidate answer")
                        task = CVDPTask(
                            "score",
                            ["cid010"],
                            {},
                            {},
                            False,
                            {},
                            None,
                            None,
                            None,
                            "Explain the hardware",
                            "PRIVATE_REFERENCE_SENTINEL",
                        )
                        result = comprehension_evaluate(task, root, candidate, 30, settings, image)
                        self.assertEqual(result.status, expected, result)
                        if expected == Status.SCORED:
                            self.assertEqual(result.score.value, 0.85)
            finally:
                server.shutdown()
        self.assertEqual(len(requests), 2)
        for request in requests:
            self.assertEqual(request["model"], "transport-smoke")
            self.assertIn("Semantic similarity", request["messages"][0]["content"])
            self.assertIn("PRIVATE_REFERENCE_SENTINEL", request["messages"][1]["content"])
            self.assertIn("Independent candidate answer", request["messages"][1]["content"])


if __name__ == "__main__":
    unittest.main()
