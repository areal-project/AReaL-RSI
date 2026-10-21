"""Offline deployment tests: no model imports, GPU allocations or judge calls."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

CODE = Path(__file__).resolve().parents[1]
WORK = CODE.parents[2] / "scale_rsi_bench"
TASKS = ("nanogpt", "opsd", "jailbreak", "swarm")
EVAL = CODE / "opsd_env/scripts/eval_official.py"


class DeploymentTests(unittest.TestCase):
    def test_bridge_routes_a_run_into_its_preallocated_slurm_job(self):
        job_path = CODE / "sandbox/job.py"
        spec = importlib.util.spec_from_file_location("scale_sandbox_job_route", job_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "lab/runs/research_astra/execution-inputs/job/workspace"
            workspace.mkdir(parents=True)
            (root / ".allocation-routes.json").write_text(
                json.dumps({"run_ids": {"research_astra": "12345"}})
            )
            self.assertEqual(module.allocation_job_id(workspace, root), "12345")

    def test_bridge_splits_researchers_across_two_preallocated_jobs(self):
        job_path = CODE / "sandbox/job.py"
        spec = importlib.util.spec_from_file_location("scale_sandbox_job_pool", job_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "lab/runs/research_astra/execution-inputs/job-a/workspace"
            workspace.mkdir(parents=True)
            job = root / "lab/runs/research_astra/jobs/job-a/job.json"
            job.parent.mkdir(parents=True)
            job.write_text(json.dumps({"spec": {"metadata": {"agent_id": "research_1"}}}))
            (root / ".allocation-routes.json").write_text(json.dumps({
                "run_ids": {"research_astra": {"agents": {"research_1": "12345"}}}
            }))
            self.assertEqual(module.allocation_job_id(workspace, root), "12345")

    def test_gpu_launchers_can_reuse_a_preallocated_slurm_job(self):
        for benchmark in ("opsd", "jailbreak", "swarm"):
            source = (CODE / f"{benchmark}_env/scripts/slurm.sh").read_text()
            self.assertIn('--jobid="$RSI_SLURM_JOB_ID"', source)

    def test_opsd_bridge_uses_exclusive_four_gpu_reservation(self):
        job_path = CODE / "sandbox/job.py"
        spec = importlib.util.spec_from_file_location("scale_sandbox_job_opsd", job_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            output = root / "output"
            workspace.mkdir()
            output.mkdir()
            calls = []
            with patch.object(
                module, "run", side_effect=lambda command, *, env=None: calls.append((command, env))
            ):
                module.gpu_benchmark("opsd", workspace, output, root / "runtime")
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][1]["RSI_EXCLUSIVE"], "1")
            self.assertEqual(calls[0][1]["RSI_GPUS"], "4")
            self.assertEqual(calls[0][1]["RSI_RESERVATION"], "qwen38-27b-align")

    def test_opsd_launcher_passes_reservation_to_slurm(self):
        source = (CODE / "opsd_env/scripts/slurm.sh").read_text()
        self.assertIn('--reservation="$RSI_RESERVATION"', source)

    def test_opsd_container_preserves_slurm_gpu_mapping_for_apptainer(self):
        source = (CODE / "opsd_env/scripts/container.sh").read_text()
        self.assertIn("APPTAINERENV_CUDA_VISIBLE_DEVICES", source)
        self.assertNotIn("export SINGULARITYENV_CUDA_VISIBLE_DEVICES", source)

    def test_opsd_bridge_reserves_eval_memory_headroom(self):
        source = (CODE / "sandbox/job.py").read_text()
        self.assertIn('"--data_parallel_size 4 --gpu_memory_utilization 0.8 "', source)

    def test_jailbreak_grading_runs_on_host_without_socks_proxy(self):
        job_path = CODE / "sandbox/job.py"
        spec = importlib.util.spec_from_file_location("scale_sandbox_job", job_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            judge = runtime / "jailbreak_env/.judge-venv/bin/python"
            judge.parent.mkdir(parents=True)
            judge.touch()
            workspace = root / "workspace"
            output = root / "output"
            workspace.mkdir()
            output.mkdir()
            calls = []
            env = {
                "OPENAI_API_KEY": "secret",
                "OPENAI_BASE_URL": "https://example.invalid/v1",
                "HTTPS_PROXY": "http://127.0.0.1:1081",
                "ALL_PROXY": "socks5h://127.0.0.1:1081",
            }
            with patch.dict(os.environ, env, clear=True), patch.object(
                module, "run", side_effect=lambda command, *, env=None: calls.append((command, env))
            ):
                module.gpu_benchmark("jailbreak", workspace, output, runtime)
            self.assertEqual(len(calls), 2)
            self.assertNotIn("OPENAI_API_KEY", calls[0][1])
            self.assertEqual(calls[1][0][0], str(judge))
            self.assertEqual(calls[1][1]["HTTPS_PROXY"], env["HTTPS_PROXY"])
            self.assertNotIn("ALL_PROXY", calls[1][1])

    def test_workspace_eval_link_survives_container_bind(self):
        entry = WORK / "opsd_env/workspace/eval_official.py"
        # /workspace is a separate mount, so ../../AReaL-RSI would point at /.
        self.assertTrue(Path(os.readlink(entry)).is_absolute())
        self.assertEqual(entry.resolve(), EVAL)

    def test_unsafe_opsd_verifier_is_blocked_before_submission(self):
        env = {k: v for k, v in os.environ.items() if k not in ("RSI_ROOT", "RSI_WORK_ROOT")}
        result = subprocess.run(["bash", str(CODE / "opsd_env/run.sh"), "verify"],
                                env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("free_gpus()", result.stderr)

    def test_runtime_resolution(self):
        for task in TASKS:
            script = CODE / f"{task}_env/scripts/env_paths.py"
            spec = importlib.util.spec_from_file_location("env_paths", script)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(module.runtime_root(script), WORK / f"{task}_env")
            with patch.dict(os.environ, {"RSI_WORK_ROOT": "/tmp/custom"}, clear=True):
                self.assertEqual(module.runtime_root(script), Path(f"/tmp/custom/{task}_env"))
            with patch.dict(os.environ, {"RSI_ROOT": "/tmp/one"}, clear=True):
                self.assertEqual(module.runtime_root(script), Path("/tmp/one"))

    def test_shell_roots_and_compatibility_links(self):
        env = {k: v for k, v in os.environ.items() if k not in ("RSI_ROOT", "RSI_WORK_ROOT")}
        for task in TASKS:
            root = WORK / f"{task}_env"
            for helper in (root / "scripts/env_root.sh", CODE / f"{task}_env/scripts/env_root.sh"):
                actual = subprocess.check_output(
                    ["bash", "-c", 'source "$1"; printf "%s" "$ROOT"', "bash", str(helper)],
                    env=env, text=True,
                )
                self.assertEqual(actual, str(root))
            for script in (CODE / f"{task}_env/scripts").iterdir():
                if script.is_file() and script.name != "eval_official.py":
                    self.assertEqual((root / "scripts" / script.name).resolve(), script)

    def test_gpu_allocation_mapping(self):
        tree = ast.parse(EVAL.read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "allocated_gpu_ids")
        namespace = {"os": os}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(EVAL), "exec"), namespace)
        resolve = namespace["allocated_gpu_ids"]
        for visible, count, expected in [
            ("4,5,6,7", 4, ["4", "5", "6", "7"]),
            ("7,2,5,1", 2, ["7", "2"]),
            ("GPU-abc-123,GPU-def-456", 2, ["GPU-abc-123", "GPU-def-456"]),
            ("MIG-GPU-abc/1/0,MIG-GPU-def/2/0", 2, ["MIG-GPU-abc/1/0", "MIG-GPU-def/2/0"]),
        ]:
            with patch.dict(os.environ, {"CUDA_VISIBLE_DEVICES": visible}, clear=True):
                self.assertEqual(resolve(count), expected)
        for visible in (None, "", "-1", "0,0", "0,1", "0,,1,2", "all", "0,1,2,garbage"):
            env = {} if visible is None else {"CUDA_VISIBLE_DEVICES": visible}
            with self.subTest(visible=visible), patch.dict(os.environ, env, clear=True):
                with self.assertRaises(RuntimeError):
                    resolve(4)

    def test_only_deployment_logic_differs_from_upstream(self):
        original = WORK / "opsd_env/source/tasks/on-policy-self-distillation/environment/workspace/eval_official.py"
        upstream, deployed = ast.parse(original.read_text()), ast.parse(EVAL.read_text())
        def definitions(tree):
            return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        before, after = definitions(upstream), definitions(deployed)
        for name in before.keys() - {"generate_all", "main"}:
            self.assertEqual(ast.dump(before[name]), ast.dump(after[name]), name)
        # main differs only in the CLI help string, not protocol parameters.
        for tree in (before["main"], after["main"]):
            for node in ast.walk(tree):
                if isinstance(node, ast.keyword) and node.arg == "help":
                    node.value = ast.Constant(value="help")
        self.assertEqual(ast.dump(before["main"]), ast.dump(after["main"]))
        def frozen(tree):
            return next(n for n in tree.body if isinstance(n, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == "FROZEN" for t in n.targets))
        self.assertEqual(ast.dump(frozen(upstream)), ast.dump(frozen(deployed)))

    def test_spawned_workers_use_allocated_ids_and_preserve_shards(self):
        tree = ast.parse(EVAL.read_text())
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                 and n.name in ("allocated_gpu_ids", "generate_all")]
        seen = []

        class Process:
            exitcode = 0

            def __init__(self, target, args):
                self.args = args

            def start(self):
                gpu, model, shard, sampling, seed, mem, path = self.args
                seen.append((gpu, shard, sampling, seed, mem))
                Path(path).write_text(json.dumps({str(i): [p] for i, p in shard}))

            def join(self):
                pass

        class Context:
            def Process(self, **kwargs):
                return Process(**kwargs)

        class MP:
            def get_context(self, method):
                assert method == "spawn"
                return Context()

        namespace = {"os": os, "json": json, "mp": MP(), "_dp_worker": object()}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(EVAL), "exec"), namespace)
        prompts = [f"prompt-{i}" for i in range(7)]
        with patch.dict(os.environ, {"CUDA_VISIBLE_DEVICES": "7,3,6,2"}, clear=True):
            actual = namespace["generate_all"]("model", prompts, {"n": 12}, 20260610, 4, 0.9)
        self.assertEqual(actual, [[p] for p in prompts])
        self.assertEqual([item[0] for item in seen], ["7", "3", "6", "2"])
        for i, (_, shard, sampling, seed, mem) in enumerate(seen):
            self.assertEqual(shard, list(enumerate(prompts))[i::4])
            self.assertEqual((sampling, seed, mem), ({"n": 12}, 20260610, 0.9))


if __name__ == "__main__":
    unittest.main()
