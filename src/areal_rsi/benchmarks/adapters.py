"""Trusted built-in adapters for supported Benchmark manifests."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from areal_rsi.base.storage import FileContentStore
from areal_rsi.benchmarks.catalog import BenchmarkBundle, BenchmarkDescriptor
from areal_rsi.modules.benchmark import AccuracyEvaluation, ArithmeticBenchmark, ArithmeticVerifier
from areal_rsi.modules.gsm8k import Gsm8kBenchmark, Gsm8kVerifier


class ArithmeticAdapter:
    def create(
        self,
        descriptor: BenchmarkDescriptor,
        config: Mapping[str, str | int],
        content: FileContentStore,
    ) -> BenchmarkBundle:
        if config:
            raise ValueError("Arithmetic Benchmark 不接受配置")
        _validate_components(descriptor, verifier="arithmetic-v1", evaluation="accuracy-v1")
        benchmark = ArithmeticBenchmark()
        return BenchmarkBundle(
            descriptor,
            benchmark.name,
            benchmark,
            ArithmeticVerifier(content),
            AccuracyEvaluation(),
        )


class Gsm8kAdapter:
    def create(
        self,
        descriptor: BenchmarkDescriptor,
        config: Mapping[str, str | int],
        content: FileContentStore,
    ) -> BenchmarkBundle:
        _validate_components(descriptor, verifier="gsm8k-v1", evaluation="accuracy-v1")
        data_path = config["data_path"]
        train_limit = config["train_limit"]
        validate_limit = config["validate_limit"]
        if (
            not isinstance(data_path, str)
            or type(train_limit) is not int
            or type(validate_limit) is not int
        ):
            raise ValueError("GSM8K Adapter 收到未校验配置")
        benchmark = Gsm8kBenchmark(
            Path(data_path), train_limit=train_limit, validate_limit=validate_limit
        )
        return BenchmarkBundle(
            descriptor,
            benchmark.name,
            benchmark,
            Gsm8kVerifier(content, benchmark),
            AccuracyEvaluation(),
        )


def _validate_components(
    descriptor: BenchmarkDescriptor, *, verifier: str, evaluation: str
) -> None:
    # A manifest may describe components, but only the trusted adapter decides valid pairings.
    if descriptor.verifier != verifier or descriptor.evaluation != evaluation:
        raise ValueError(f"Benchmark {descriptor.benchmark_id} 的组件组合不受 Adapter 支持")
