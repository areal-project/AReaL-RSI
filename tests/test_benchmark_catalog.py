from __future__ import annotations

import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path

from areal_rsi.api.base import (
    BenchmarkSplit,
    TaskResult,
    TrajectoryRef,
    VerificationRequest,
    VerifierSpec,
)
from areal_rsi.base.storage import FileContentStore, FileVersionManager, json_bytes
from areal_rsi.benchmarks import BenchmarkCatalog, create_builtin_registry, create_registry
from areal_rsi.benchmarks.catalog import BenchmarkBundle, BenchmarkDescriptor
from areal_rsi.modules.benchmark import AccuracyEvaluation, ArithmeticBenchmark, ArithmeticVerifier
from areal_rsi.modules.gsm8k import Gsm8kBenchmark, Gsm8kVerifier, prepare_dataset


class BenchmarkCatalogTest(unittest.IsolatedAsyncioTestCase):
    def test_external_registry_does_not_modify_builtin_registry(self) -> None:
        descriptor = BenchmarkDescriptor(
            "external-fixture", "v1", "External", "fixture", "external-fixture",
            "embedded", "external-fixture", "accuracy-v1", (),
        )

        class ExternalAdapter:
            def create(
                self,
                descriptor: BenchmarkDescriptor,
                config: Mapping[str, str | int],
                content: FileContentStore,
            ) -> BenchmarkBundle:
                return BenchmarkBundle(
                    descriptor, "external-fixture", ArithmeticBenchmark(),
                    ArithmeticVerifier(content), AccuracyEvaluation(),
                )

        with tempfile.TemporaryDirectory() as temporary:
            content = FileContentStore(Path(temporary) / "content")
            registry = create_registry(
                content,
                BenchmarkCatalog((descriptor,)),
                {"external-fixture": ExternalAdapter()},
            )
            bundle = registry.create("external-fixture")
            self.assertEqual(bundle.instance_id, "external-fixture")
            with self.assertRaisesRegex(ValueError, "未知 Benchmark"):
                create_builtin_registry(content).create("external-fixture")

    async def test_gsm8k_adapter_matches_direct_implementation(self) -> None:
        raw = b"\n".join(
            json_bytes(
                {"question": f"Synthetic fixture {index}", "answer": f"reasoning\n#### {index}"}
            )
            for index in range(30)
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = root / "dataset"
            prepare_dataset(raw, dataset, source="synthetic-adapter-test")
            content = FileContentStore(root / "content")

            direct = Gsm8kBenchmark(dataset, train_limit=4, validate_limit=3)
            direct_verifier = Gsm8kVerifier(content, direct)
            bundle = create_builtin_registry(content).create(
                "gsm8k",
                {"data_path": str(dataset), "train_limit": 4, "validate_limit": 3},
            )

            self.assertEqual(bundle.instance_id, direct.name)
            for split in BenchmarkSplit:
                self.assertEqual(
                    await bundle.benchmark.list_tasks(split), await direct.list_tasks(split)
                )

            versions = FileVersionManager(root / "versions")
            ref = await content.put_text("fixture")
            solution = await versions.create_solution(
                await versions.create_model("m", ref), await versions.create_harness("h", ref)
            )
            task = (await direct.list_tasks(BenchmarkSplit.TRAIN))[0]
            expected = task.task_id.rsplit("_", 1)[1]
            output = await content.put_json(
                {
                    "task_id": task.task_id,
                    "solution_id": solution.solution_id,
                    "response": f"#### {expected}",
                }
            )
            result = TaskResult(output, TrajectoryRef("trace"))
            request = VerificationRequest(
                task,
                solution,
                result,
                VerifierSpec(bundle.descriptor.verifier, "injected"),
            )
            direct_result = await direct_verifier.verify(request)
            adapter_result = await bundle.verifier.verify(request)
            self.assertEqual(adapter_result, direct_result)

    def test_environment_configuration_is_typed_and_validated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            content = FileContentStore(Path(temporary) / "content")
            registry = create_builtin_registry(content)
            with self.assertRaisesRegex(ValueError, "train_limit 必须为整数"):
                registry.create(
                    "gsm8k",
                    environment={
                        "RSI_GSM8K_DATA": "/tmp/not-used",
                        "RSI_TRAIN_LIMIT": "not-an-integer",
                        "RSI_VALIDATE_LIMIT": "1",
                    },
                )
            with self.assertRaisesRegex(ValueError, "未知 Benchmark 配置"):
                registry.create("arithmetic-v1", {"target": "harness"})
