"""Synthetic protocol/target-export tests; no target API or CUDA is invoked."""

from __future__ import annotations

import hashlib
import json
import runpy
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from areal_rsi.adapters.kernelbench import (
    aggregate_results,
    check_edit_scope,
    mock_results,
    prepare_dataset,
    read_manifest,
)
from areal_rsi.api.base import SolutionVersion, Task, TaskResult, TrajectoryRef
from areal_rsi.base.storage import FileContentStore, FileVersionManager
from areal_rsi.modules.runtime import DshAgentRuntime
from areal_rsi.modules.task_execution import FileTaskExecutor, TargetGateway


def git_fixture(root: Path) -> str:
    directory = root / "KernelBench/level1"
    directory.mkdir(parents=True)
    for problem in range(1, 4):
        (directory / f"{problem}_Synthetic.py").write_text(
            f"# Synthetic task {problem}\nclass Model: pass\n"
        )
    for command in (
        ["git", "init", "-q"],
        ["git", "add", "KernelBench"],
        [
            "git",
            "-c",
            "user.name=Synthetic Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--no-gpg-sign",
            "-qm",
            "synthetic",
        ],
    ):
        subprocess.run(command, cwd=root, check=True, capture_output=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


class KernelbenchTest(unittest.TestCase):
    def test_protocol_description_never_generates_a_candidate(self) -> None:
        script = Path(__file__).resolve().parents[1] / "examples/kernelbench/run.py"
        describe = runpy.run_path(str(script))["describe_identity"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            revision = git_fixture(root / "repo")
            selection = {
                s: [{"level": 1, "problem_id": i}]
                for i, s in enumerate(("development", "search", "final"), 1)
            }
            prepare_dataset(
                root / "repo",
                root / "prepared",
                selection,
                revision=revision,
                dataset_kind="synthetic",
            )
            config = root / "operator.json"
            config.write_text(
                json.dumps(
                    {
                        "prepared_data": str(root / "prepared"),
                        "gpu": {"kind": "local", "python": "python3.10", "repository": "/gpu/repo"},
                        "target_budget": {"max_model_requests": 12, "max_output_tokens": 4096},
                    }
                )
            )

            def probe(command: list[str], **kwargs: Any) -> None:
                self.assertIn("--describe", command)
                self.assertNotIn("--generation", command)
                budget = json.loads(command[command.index("--target-budget") + 1])
                self.assertEqual(budget["max_model_requests"], 12)
                self.assertEqual(budget["max_output_tokens_per_request"], 4096)
                destination = Path(command[command.index("--output") + 1])
                destination.write_text(
                    json.dumps(
                        {
                            "protocol_id": "synthetic-environment",
                            "environment": {"synthetic": True},
                        }
                    )
                )

            with patch("subprocess.run", side_effect=probe):
                result = describe(config, "validate", "fixed-target")
            manifest = read_manifest(root / "prepared/private/search/manifest.json")
            self.assertEqual(result["manifest_id"], manifest["manifest_id"])
            self.assertEqual(result["phase"], "search")
            self.assertEqual(result["target_model"], "fixed-target")
            self.assertNotIn("score", result)

    def test_gpu_worker_distinguishes_bad_code_from_retryable_infrastructure(self) -> None:
        worker = runpy.run_path(
            str(Path(__file__).resolve().parents[1] / "examples/kernelbench/evaluate.py")
        )["worker"]
        modules: dict[str, Any] = {
            name: ModuleType(name)
            for name in (
                "torch",
                "kernelbench",
                "kernelbench.eval",
                "kernelbench.timing",
                "kernelbench.kernel_static_checker",
            )
        }
        for dtype in ("float32", "float16", "bfloat16"):
            setattr(modules["torch"], dtype, dtype)
        modules["torch"].device = lambda value: value
        modules["kernelbench.timing"].measure_ref_program_time = lambda **kwargs: {"mean": 1.0}
        modules["kernelbench.kernel_static_checker"].validate_kernel_static = (
            lambda *args, **kwargs: (True, [], [])
        )

        def bad_candidate(*args: Any, **kwargs: Any) -> None:
            raise ValueError("Synthetic malformed candidate output")

        with tempfile.TemporaryDirectory() as temporary:
            for name, callback, expected in (
                ("bad-code", bad_candidate, "invalid_kernel"),
                ("retry", lambda *args, **kwargs: None, "infra_error"),
            ):
                root = Path(temporary) / name
                root.mkdir()
                reference, kernel = root / "reference.py", root / "model_new.py"
                reference.write_text("class Model: pass\n")
                kernel.write_text("class ModelNew: pass\n")
                request = {
                    "identity": {"simulated": True},
                    "task_id": "level1_1",
                    "repository": str(root),
                    "reference": str(reference),
                    "kernel": str(kernel),
                    "reference_sha256": hashlib.sha256(reference.read_bytes()).hexdigest(),
                    "kernel_sha256": hashlib.sha256(kernel.read_bytes()).hexdigest(),
                    "backend": "cuda",
                    "precision": "fp32",
                    "seed": 42,
                    "num_correct_trials": 5,
                    "num_perf_trials": 100,
                    "timing_method": "cuda_event",
                }
                path = root / "request.json"
                path.write_text(json.dumps(request))
                modules["kernelbench.eval"].eval_kernel_against_ref = callback
                with patch.dict(sys.modules, modules):
                    worker(path, root / "result.json")
                self.assertEqual(json.loads((root / "result.json").read_text())["status"], expected)

    def test_gpu_preflight_failure_writes_incomplete_results(self) -> None:
        from areal_rsi.adapters.kernelbench import REVISION

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest: dict[str, Any] = {
                "schema": "kernelbench-rsi-v1",
                "revision": REVISION,
                "split": "search",
                "dataset_kind": "synthetic",
                "tasks": [{"task_id": "level1_1"}],
            }
            manifest["manifest_id"] = hashlib.sha256(
                json.dumps(manifest, sort_keys=True).encode()
            ).hexdigest()
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest))
            generation = root / "generation"
            generation.mkdir()
            target = {"model": "synthetic-target", "base_url": "http://invalid.local"}
            (generation / "generation.json").write_text(
                json.dumps(
                    {
                        "manifest_id": manifest["manifest_id"],
                        "harness_id": "h1",
                        "target": target,
                        "budget": {"max_model_requests": 1},
                        "tasks": [
                            {
                                "task_id": "level1_1",
                                "producer": "task-agent",
                                "target": target,
                                "harness_id": "h1",
                                "status": "generated",
                            }
                        ],
                    }
                )
            )
            script = Path(__file__).resolve().parents[1] / "examples/kernelbench/evaluate.py"
            output = root / "results.json"
            subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--repository",
                    str(root / "not-a-repo"),
                    "--manifest",
                    str(path),
                    "--generation",
                    str(generation),
                    "--output",
                    str(output),
                ],
                check=True,
                capture_output=True,
            )
            rows = json.loads(output.read_text())
            self.assertEqual(rows[0]["status"], "infra_error")
            aggregate = aggregate_results(manifest, rows)
            self.assertIsNone(aggregate.fast_1)
            self.assertFalse(aggregate.simulated)
            described = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--describe",
                    "--repository",
                    str(root),
                    "--target-budget",
                    "{}",
                    "--output",
                    str(root / "identity.json"),
                ],
                capture_output=True,
            )
            self.assertNotEqual(described.returncode, 0)
            self.assertFalse((root / "identity.json").exists())

    def test_pinned_preparation_disjoint_pools_and_tamper_detection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            revision = git_fixture(root / "repo")
            selection = {
                s: [{"level": 1, "problem_id": i}]
                for i, s in enumerate(("development", "search", "final"), 1)
            }
            output = root / "prepared"
            protocol = prepare_dataset(
                root / "repo", output, selection, revision=revision, dataset_kind="synthetic"
            )
            self.assertEqual(protocol["splits"]["search"]["count"], 1)
            self.assertFalse((output / "search").exists())
            development = read_manifest(output / "development/manifest.json")
            self.assertEqual(development["tasks"][0]["task_id"], "level1_1")
            self.assertTrue((output / "private/search/references/level1_2.py").is_file())
            # The committed blob, rather than a modified worktree, is extracted.
            (root / "repo/KernelBench/level1/1_Synthetic.py").write_text("modified")
            self.assertIn(
                "Synthetic task 1", (output / "development/references/level1_1.py").read_text()
            )
            selection["search"] = selection["development"]
            with self.assertRaisesRegex(ValueError, "disjoint"):
                prepare_dataset(
                    root / "repo",
                    root / "duplicate",
                    selection,
                    revision=revision,
                    dataset_kind="synthetic",
                )
            (output / "development/references/level1_1.py").write_text("tampered")
            with self.assertRaisesRegex(ValueError, "changed"):
                read_manifest(output / "development/manifest.json")

    def test_aggregate_full_denominator_and_incomplete_infrastructure(self) -> None:
        manifest = {
            "manifest_id": "synthetic",
            "tasks": [{"task_id": f"task-{i}"} for i in range(4)],
        }
        outcomes = {
            "task-0": "correct_fast",
            "task-1": "correct_slow",
            "task-2": "wrong",
            "task-3": "compile_error",
        }
        rows = mock_results(manifest, harness_id="h1", outcomes=outcomes)
        result = aggregate_results(manifest, rows)
        self.assertEqual((result.fast_1, result.correctness, result.compiled), (0.25, 0.5, 0.75))
        self.assertTrue(result.simulated)
        rows[3]["status"] = "infra_error"
        result = aggregate_results(manifest, rows)
        self.assertIsNone(result.fast_1)
        self.assertEqual(result.status, "incomplete")
        self.assertEqual(result.task_count, 4)
        self.assertEqual(result.infra_errors, 1)
        for bad in (rows[:-1], [*rows[:-1], rows[0]]):
            with self.assertRaises(ValueError):
                aggregate_results(manifest, bad)

    def test_invalid_timings_and_mixed_protocols_are_rejected(self) -> None:
        manifest = {"manifest_id": "synthetic", "tasks": [{"task_id": "a"}, {"task_id": "b"}]}
        for change in (
            {"runtime_ms": float("nan")},
            {"runtime_ms": -1},
            {"protocol_id": "another"},
            {"simulated": False},
            {"manifest_id": "another"},
        ):
            rows = mock_results(manifest, harness_id="h1")
            rows[0].update(change)
            with self.assertRaises(ValueError, msg=str(change)):
                aggregate_results(manifest, rows)

    def test_skill_scope_checks_real_files(self) -> None:
        seed = Path(__file__).resolve().parents[1] / "examples/kernelbench/seed"
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / "candidate"
            shutil.copytree(seed, candidate)
            (candidate / "skills/kernel-optimization.md").write_text(
                "different generic instructions"
            )
            self.assertTrue(check_edit_scope(seed, candidate, "skills"))
            (candidate / "prompt.md").write_text("out of scope")
            with self.assertRaisesRegex(ValueError, "skills scope"):
                check_edit_scope(seed, candidate, "skills")
            self.assertTrue(check_edit_scope(seed, candidate, "harness"))

    def test_target_gateway_binds_model_and_enforces_request_budget(self) -> None:
        forwarded: list[dict[str, Any]] = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                forwarded.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                body = b'{"choices":[{"message":{"role":"assistant","content":"fixture"}}]}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        gateway = TargetGateway(
            f"http://127.0.0.1:{server.server_port}",
            "synthetic-fixture-key",
            model="fixed-target",
            max_requests=1,
            max_output_tokens=32,
            timeout_seconds=3,
        )

        def call(model: str) -> None:
            request = urllib.request.Request(
                gateway.url + "/chat/completions",
                data=json.dumps(
                    {"model": model, "max_tokens": 999, "messages": [], "n": 9}
                ).encode(),
                headers={
                    "Authorization": f"Bearer {gateway.token}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                self.assertEqual(response.status, 200)

        try:
            # Even if a candidate discovers the internal compatibility port,
            # neither anonymous requests nor the outer task token can bypass
            # model selection or the host-owned request counter.
            for token in (None, gateway.token):
                request = urllib.request.Request(
                    gateway.delegate.url + "/chat/completions",
                    data=json.dumps({"model": "research-model", "messages": []}).encode(),
                    headers={"Authorization": "Bearer " + token} if token else {},
                )
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(request, timeout=5)
                self.assertEqual(denied.exception.code, 401)
            self.assertEqual(forwarded, [])
            with self.assertRaises(urllib.error.HTTPError):
                call("research-model")
            self.assertEqual(gateway.calls, 0)
            call("fixed-target")
            self.assertEqual(forwarded[0]["max_tokens"], 32)
            self.assertEqual(forwarded[0]["n"], 1)
            with self.assertRaises(urllib.error.HTTPError):
                call("fixed-target")
            self.assertTrue(gateway.exhausted)
            self.assertEqual(len(forwarded), 1)
        finally:
            gateway.close()
            server.shutdown()
            server.server_close()


class KernelExportTest(unittest.IsolatedAsyncioTestCase):
    async def test_wrong_environment_target_is_rejected_before_any_model_call(self) -> None:
        from areal_rsi.adapters.kernelbench import generate

        seed = Path(__file__).resolve().parents[1] / "examples/kernelbench/seed"
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "generation"
            with (
                patch("areal_rsi.adapters.kernelbench.read_manifest", return_value={"tasks": []}),
                patch(
                    "areal_rsi.config.ModelConfig.from_environment",
                    return_value=SimpleNamespace(model="other-model"),
                ),
                patch("areal_rsi.modules.task_execution.TargetGateway") as gateway,
                self.assertRaisesRegex(ValueError, "pinned target"),
            ):
                await generate(
                    Path(temporary) / "unused.json",
                    seed,
                    destination,
                    expected_target_model="fixed-target",
                )
            gateway.assert_not_called()
            self.assertFalse(destination.exists())

    async def test_only_diagnostic_releases_development_traces(self) -> None:
        from areal_rsi.api.base import Trajectory
        from areal_rsi.base.storage import FileTrajectoryStore, encode, now

        script = Path(__file__).resolve().parents[1] / "examples/kernelbench/run.py"
        entry = runpy.run_path(str(script))["run"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            revision = git_fixture(root / "repo")
            selection = {
                s: [{"level": 1, "problem_id": i}]
                for i, s in enumerate(("development", "search", "final"), 1)
            }
            prepare_dataset(
                root / "repo",
                root / "prepared",
                selection,
                revision=revision,
                dataset_kind="synthetic",
            )
            config = root / "operator.json"
            config.write_text(
                json.dumps({"prepared_data": str(root / "prepared"), "gpu": {"kind": "mock"}})
            )

            async def target(
                manifest_path: Path, harness: Path, destination: Path, **kwargs: Any
            ) -> dict[str, Any]:
                self.assertEqual(kwargs["expected_target_model"], "fixed-target")
                manifest = read_manifest(manifest_path)
                content = FileContentStore(destination / "content")
                trajectories = FileTrajectoryStore(destination / "trajectories", content)
                task_id = manifest["tasks"][0]["task_id"]
                trace_id = "trajectory_" + task_id
                ref = await trajectories.save(
                    Trajectory(
                        trace_id,
                        "task-agent",
                        await content.put_json(
                            {
                                "turns": [
                                    {
                                        "role": "assistant",
                                        "content": "synthetic " + manifest["split"] + " trace",
                                    }
                                ],
                            }
                        ),
                        {},
                        now(),
                    )
                )
                session = destination / "sessions" / task_id / trace_id
                session.mkdir(parents=True)
                (session / "configuration.json").write_text("PRIVATE-CONFIG-CANARY")
                exported = destination / "tasks" / task_id
                exported.mkdir(parents=True)
                (exported / "model_new.py").write_text("# synthetic target output")
                record: dict[str, Any] = {
                    "task_id": task_id,
                    "status": "generated",
                    "trajectory": encode(ref),
                    "output": encode(
                        await content.put_json({"response": "Written to model_new.py"})
                    ),
                }
                if manifest["split"] == "development":
                    # A failed task still has its session trajectory, even if
                    # no TaskResult returned its reference to the generator.
                    record.pop("trajectory")
                    record["status"] = "budget_exhausted"
                return {
                    "harness_id": "synthetic-harness",
                    "target": {"model": "fixed-target"},
                    "tasks": [record],
                }

            for phase in ("diagnostic", "search", "final"):
                output = root / phase
                with patch.dict(entry.__globals__, {"generate": target}):
                    result = await entry(root / "unused", output, config, phase, "fixed-target")
                self.assertTrue(result["simulated"])
                if phase == "diagnostic":
                    public = output / "public/development"
                    summary = json.loads((public / "summary.json").read_text())
                    self.assertEqual(summary["tasks"][0]["status"], "budget_exhausted")
                    self.assertTrue((public / "tasks/level1_1/trajectory_level1_1.json").is_file())
                    self.assertTrue((public / "tasks/level1_1/model_new.py").is_file())
                    self.assertTrue((public / "tasks/level1_1/output.json").is_file())
                    released = "".join(p.read_text() for p in public.rglob("*") if p.is_file())
                    self.assertNotIn("PRIVATE-CONFIG-CANARY", released)
                    self.assertNotIn("synthetic search trace", released)
                else:
                    self.assertFalse((output / "public").exists())
            config.write_text(
                json.dumps(
                    {
                        "prepared_data": str(root / "prepared"),
                        "gpu": {"kind": "mock"},
                        "phases": {"diagnostic": "search"},
                    }
                )
            )
            with self.assertRaisesRegex(ValueError, "public development"):
                await entry(root / "unused", root / "bad", config, "diagnostic")

    async def test_trusted_runner_schema_with_mock_gpu_and_stub_target(self) -> None:
        script = Path(__file__).resolve().parents[1] / "examples/kernelbench/run.py"
        entry = runpy.run_path(str(script))["run"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            revision = git_fixture(root / "repo")
            selection = {
                s: [{"level": 1, "problem_id": i}]
                for i, s in enumerate(("development", "search", "final"), 1)
            }
            prepared = root / "prepared"
            prepare_dataset(
                root / "repo", prepared, selection, revision=revision, dataset_kind="synthetic"
            )
            config = root / "operator.json"
            config.write_text(json.dumps({"prepared_data": str(prepared), "gpu": {"kind": "mock"}}))

            async def target(
                manifest_path: Path, harness: Path, destination: Path, **kwargs: Any
            ) -> dict[str, Any]:
                manifest = read_manifest(manifest_path)
                destination.mkdir(parents=True)
                return {
                    "harness_id": "fixture-harness",
                    "target": {"model": "stub-target"},
                    "tasks": [
                        {"task_id": row["task_id"], "status": "generated"}
                        for row in manifest["tasks"]
                    ],
                }

            with patch.dict(entry.__globals__, {"generate": target}):
                result = await entry(root / "unused-harness", root / "output", config, "validate")
            self.assertTrue(result["simulated"])
            self.assertEqual(result["metric"], "fast_1")
            self.assertEqual(result["split"], "search")
            self.assertEqual(result["phase"], "search")
            self.assertEqual(result["score"], 1)
            self.assertTrue((root / "output/evaluation.json").is_file())
            # A legacy operator config remains accepted through the new
            # canonical search phase used by ResearchExecution.
            config.write_text(
                json.dumps(
                    {
                        "prepared_data": str(prepared),
                        "gpu": {"kind": "mock"},
                        "phases": {
                            "diagnostic": "development",
                            "validate": "search",
                            "final": "final",
                        },
                    }
                )
            )
            with patch.dict(entry.__globals__, {"generate": target}):
                canonical = await entry(
                    root / "unused-harness", root / "canonical-output", config, "search"
                )
            self.assertEqual((canonical["phase"], canonical["split"]), ("search", "search"))

    async def test_only_target_workspace_file_is_exported_with_trace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            content = FileContentStore(root / "content")
            versions = FileVersionManager(root / "versions")
            ref = await content.put_text("synthetic")
            solution = await versions.create_solution(
                await versions.create_model("fixed-target", ref),
                await versions.create_harness("h", ref),
            )
            outcome = TaskResult(
                await content.put_json({"response": "do not parse chat code"}),
                TrajectoryRef("actual-task-trace"),
            )
            outside = root / "model_new.py"
            outside.write_text("unrelated candidate")

            class FakeHarness:
                def __init__(self, runtime: Any, content: Any, workspaces: Path):
                    self.workspaces = workspaces

                async def run(self, solution: SolutionVersion, task: Task) -> TaskResult:
                    workspace = self.workspaces / "task-fixture"
                    workspace.mkdir(parents=True)
                    (workspace / "model_new.py").write_text(
                        "# target-generated fixture\nclass ModelNew: pass\n"
                    )
                    return outcome

            with patch("areal_rsi.modules.task_execution.LocalHarnessRuntime", FakeHarness):
                exported = await FileTaskExecutor(
                    cast(DshAgentRuntime, None), content, root / "execution"
                ).run(
                    solution,
                    Task("t", {}),
                    exports=("model_new.py", "absent.py"),
                    timeout_seconds=2,
                )
            self.assertEqual(exported.result.trajectory.trajectory_id, "actual-task-trace")
            self.assertEqual(exported.missing, ("absent.py",))
            self.assertIn(
                "target-generated", await content.read_text(exported.files["model_new.py"])
            )
            self.assertEqual(outside.read_text(), "unrelated candidate")


if __name__ == "__main__":
    unittest.main()
