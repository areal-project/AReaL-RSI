from __future__ import annotations

import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path

from areal_rsi.infra.storage.files import FileContentStore
from areal_rsi.infra.tasks import BenchmarkCatalog, create_builtin_registry, create_registry
from areal_rsi.infra.tasks.arithmetic import (
    AccuracyEvaluation,
    ArithmeticBenchmark,
    ArithmeticVerifier,
)
from areal_rsi.infra.tasks.catalog import BenchmarkBundle, BenchmarkDescriptor


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
