import tempfile
import unittest
from pathlib import Path

from areal_rsi.api.records import (
    BenchmarkSplit,
    TaskResult,
    TrajectoryRef,
    VerificationRequest,
    VerifierSpec,
)
from areal_rsi.infra.storage.files import FileContentStore, FileVersionManager
from areal_rsi.infra.tasks.arithmetic import ArithmeticBenchmark, ArithmeticVerifier


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
            spec = VerifierSpec("arithmetic-verifier", "injected")
            request = VerificationRequest(
                task,
                solution,
                TaskResult(output, TrajectoryRef("t")),
                spec,
            )
            result = await verifier.verify(request)
            self.assertFalse(result.passed)
            self.assertEqual(result.score, 0)
            self.assertNotIn("21", result.feedback)
            bad = await content.put_json(
                {"task_id": task.task_id, "solution_id": "unrelated", "response": "#### 21"}
            )
            with self.assertRaises(ValueError):
                await verifier.verify(
                    VerificationRequest(
                        task,
                        solution,
                        TaskResult(bad, TrajectoryRef("t")),
                        spec,
                    )
                )
