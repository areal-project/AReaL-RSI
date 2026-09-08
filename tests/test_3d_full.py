"""Offline checks for the external one-round workflow glue."""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from areal_rsi.api.base import (
    BenchmarkSplit,
    EvolutionTarget,
    ExperimentPlan,
    Task,
    TrajectoryRef,
)
from areal_rsi.base.storage import FileContentStore, FileVersionManager


class FullFlowTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        example = Path(__file__).resolve().parents[1] / "examples/3d_bench"
        with patch.object(sys, "path", [str(example), *sys.path]):
            spec = importlib.util.spec_from_file_location("full_example", example / "run_full.py")
            assert spec is not None and spec.loader is not None
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)

    async def test_explicit_diagnostic_identity(self) -> None:
        task = Task("MODEL-001", "public target")
        first = self.module.DiagnosticBenchmark(task, {"revision": "a"})
        self.assertIn("diagnostic", first.name)
        self.assertEqual(
            await first.list_tasks(BenchmarkSplit.TRAIN),
            await first.list_tasks(BenchmarkSplit.VALIDATE),
        )
        self.assertNotEqual(
            first.name, self.module.DiagnosticBenchmark(task, {"revision": "b"}).name
        )

    def test_plan_cannot_expand_user_authorization(self) -> None:
        plan = ExperimentPlan(
            "goal", "harness-evolution", "bench", "base", EvolutionTarget.HARNESS, 1
        )
        self.module.check_plan(plan, "base", "bench")
        plan.max_iterations = 2
        with self.assertRaises(ValueError):
            self.module.check_plan(plan, "base", "bench")

    async def test_file_runtime_message_output_and_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            template = root / "template"
            (template / "src").mkdir(parents=True)
            (template / "src/model.js").write_text("export function createModel() {}")
            (template / "package.json").write_text('{"type":"module"}')
            (template / "CONTRACT_V2.md").write_text("public contract")
            content = FileContentStore(root / "content")
            versions = FileVersionManager(root / "versions")
            harness_dir = root / "harness"
            harness_dir.mkdir()
            (harness_dir / "prompt.md").write_text("minimal harness")
            model = await versions.create_model("fake", await content.put_json({}))
            harness = await versions.create_harness("base", await content.snapshot(harness_dir))
            solution = await versions.create_solution(model, harness)
            runtime = AsyncMock()
            session = runtime.open.return_value
            session.send.return_value = "Done"
            session.finish.return_value = TrajectoryRef("real-stub-trace")
            runner = self.module.FileTaskRuntime(
                root / "tasks", content, runtime, template, lambda _: None
            )
            task = Task("MODEL-001", "target")
            result = await runner.run(solution, task, config={"execution_key": "run/train/1"})
            self.assertEqual(
                await content.read_text(result.output), "export function createModel() {}"
            )
            self.assertEqual(result.output.media_type, "text/javascript")
            session.send.assert_awaited_once_with(self.module.TASK_MESSAGE)
            cached = await runner.run(solution, task, config={"execution_key": "run/train/1"})
            self.assertEqual(cached, result)
            self.assertEqual(runtime.open.await_count, 1)
            workspace = Path(runtime.open.call_args.kwargs["workspace_root"])
            self.assertEqual(json.loads((workspace / "package.json").read_text())["type"], "module")
            self.assertFalse((workspace / "scripts").exists())
            session.send.side_effect = RuntimeError("stream closed")
            with self.assertRaisesRegex(RuntimeError, "stream closed"):
                await runner.run(solution, task, config={"execution_key": "interrupted"})
            session.send.side_effect = None
            with self.assertRaisesRegex(ValueError, "implicit paid retry"):
                await runner.run(solution, task, config={"execution_key": "interrupted"})
            runner.retry_incomplete = True
            await runner.run(solution, task, config={"execution_key": "interrupted"})
            archives = list((root / "tasks/failed-attempts").iterdir())
            self.assertEqual(len(archives), 1)
            self.assertTrue((archives[0] / "workspace/src/model.js").is_file())
            self.assertTrue((archives[0] / "inputs.json").is_file())
