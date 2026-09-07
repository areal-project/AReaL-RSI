import tempfile
import unittest
from pathlib import Path

from areal_rsi.api.base import BenchmarkSplit, TaskResult, TrajectoryRef
from areal_rsi.base.storage import FileContentStore, FileVersionManager
from areal_rsi.modules.benchmark import ArithmeticBenchmark, ArithmeticVerifier


class EvaluationTest(unittest.IsolatedAsyncioTestCase):
    async def test_wrong_answer_remains_wrong_and_solution_is_checked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            content = FileContentStore(root / "content")
            versions = FileVersionManager(root / "versions")
            ref = await content.put_text("test")
            model = await versions.create_model("m", ref)
            harness = await versions.create_harness("h", ref)
            solution = await versions.create_solution(model, harness)
            task = (await ArithmeticBenchmark().list_tasks(BenchmarkSplit.TRAIN))[0]
            output = await content.put_json(
                {
                    "task_id": task.task_id,
                    "solution_id": solution.solution_id,
                    "response": "#### 9999",
                }
            )
            verifier = ArithmeticVerifier(content)
            result = await verifier.verify(task, solution, TaskResult(output, TrajectoryRef("t")))
            self.assertFalse(result.passed)
            self.assertEqual(result.score, 0)
            self.assertNotIn("21", result.feedback)
            bad = await content.put_json(
                {"task_id": task.task_id, "solution_id": "unrelated", "response": "#### 21"}
            )
            with self.assertRaises(ValueError):
                await verifier.verify(task, solution, TaskResult(bad, TrajectoryRef("t")))
