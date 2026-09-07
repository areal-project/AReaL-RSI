"""使用合成 fixture 离线测试数据契约；不将这些题冒充官方 GSM8K。"""

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from areal_rsi.api.base import BenchmarkSplit, TaskResult, TrajectoryRef
from areal_rsi.base.storage import FileContentStore, FileVersionManager, json_bytes
from areal_rsi.modules.gsm8k import Gsm8kBenchmark, Gsm8kVerifier, final_answer, prepare_dataset


class Gsm8kTest(unittest.IsolatedAsyncioTestCase):
    def test_answer_format(self) -> None:
        self.assertEqual(final_answer("reasoning\n#### -1,234.50"), Decimal("-1234.5"))
        for text in (
            "42",
            "#### 42abc",
            "#### 4e2",
            "#### 1,2",
            "#### 2/3",
            "#### NaN",
            "#### 2\nmore",
        ):
            self.assertIsNone(final_answer(text), text)

    async def test_partition_identity_isolation_and_verification(self) -> None:
        raw = b"\n".join(
            json_bytes(
                {"question": f"Synthetic fixture {i}", "answer": f"private reasoning\n#### {i}"}
            )
            for i in range(30)
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = root / "dataset"
            prepare_dataset(raw, dataset, source="synthetic-unit-test")
            benchmark = Gsm8kBenchmark(dataset, train_limit=4, validate_limit=3)
            train = await benchmark.list_tasks(BenchmarkSplit.TRAIN)
            validate = await benchmark.list_tasks(BenchmarkSplit.VALIDATE)
            self.assertFalse({t.task_id for t in train} & {t.task_id for t in validate})
            self.assertNotIn("private reasoning", json_bytes([*train, *validate]).decode())
            self.assertNotIn("answer", json_bytes([*train, *validate]).decode())
            larger = Gsm8kBenchmark(dataset, train_limit=8, validate_limit=3)
            self.assertNotEqual(benchmark.name, larger.name)
            self.assertEqual(validate, await larger.list_tasks(BenchmarkSplit.VALIDATE))
            store = FileContentStore(root / "content")
            versions = FileVersionManager(root / "versions")
            ref = await store.put_text("fixture")
            solution = await versions.create_solution(
                await versions.create_model("m", ref), await versions.create_harness("h", ref)
            )
            verifier = Gsm8kVerifier(store, benchmark)
            task = train[0]
            expected = task.task_id.rsplit("_", 1)[1]
            output = await store.put_json(
                {
                    "task_id": task.task_id,
                    "solution_id": solution.solution_id,
                    "response": f"#### {expected}",
                }
            )
            result = TaskResult(output, TrajectoryRef("trace"))
            self.assertTrue((await verifier.verify(task, solution, result)).passed)
            wrong = await store.put_json(
                {
                    "task_id": task.task_id,
                    "solution_id": solution.solution_id,
                    "response": "#### 99999",
                }
            )
            self.assertFalse(
                (await verifier.verify(task, solution, TaskResult(wrong, result.trajectory))).passed
            )
            task.input = "changed"
            with self.assertRaises(ValueError):
                await verifier.verify(task, solution, result)
            self.assertNotEqual(
                task.input, (await benchmark.list_tasks(BenchmarkSplit.TRAIN))[0].input
            )
            with self.assertRaises(FileExistsError):
                prepare_dataset(raw, dataset, source="synthetic-unit-test")
            with self.assertRaises(ValueError):
                Gsm8kBenchmark(dataset, train_limit=28, validate_limit=3)
            (dataset / "validate.json").write_text(json.dumps([]))
            with self.assertRaises(ValueError):
                Gsm8kBenchmark(dataset, train_limit=4, validate_limit=3)
