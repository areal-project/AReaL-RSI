"""Load declarative Benchmark metadata and assemble trusted adapters."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml  # type: ignore[import-untyped]

from areal_rsi.api.modules import Benchmark, Evaluation, Verifier
from areal_rsi.base.storage import FileContentStore

BUILTIN_MANIFEST = Path(__file__).with_name("config") / "supported_benchmarks.yaml"
_MISSING = object()


@dataclass(frozen=True, slots=True)
class BenchmarkParameter:
    name: str
    value_type: str
    environment: str | None = None
    required: bool = False
    default: str | int | None = None
    minimum: int | None = None

    def resolve(self, override: object, environment: Mapping[str, str]) -> str | int:
        value = override
        if value is _MISSING and self.environment and self.environment in environment:
            value = environment[self.environment]
        if value is _MISSING:
            value = self.default
        if value is None:
            if self.required:
                raise ValueError(f"Benchmark 缺少必填配置：{self.name}")
            raise ValueError(f"Benchmark 配置没有值：{self.name}")
        if self.value_type in {"path", "string"}:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Benchmark 配置 {self.name} 必须为非空字符串")
            return value
        if self.value_type == "integer":
            if isinstance(value, str):
                with suppress(ValueError):
                    value = int(value)
            if type(value) is not int:
                raise ValueError(f"Benchmark 配置 {self.name} 必须为整数")
            if self.minimum is not None and value < self.minimum:
                raise ValueError(f"Benchmark 配置 {self.name} 不能小于 {self.minimum}")
            return value
        raise ValueError(f"不支持的 Benchmark 参数类型：{self.value_type}")


@dataclass(frozen=True, slots=True)
class BenchmarkDescriptor:
    """Public metadata; it intentionally contains no expected answers or credentials."""

    benchmark_id: str
    version: str
    display_name: str
    description: str
    adapter: str
    data_provider: str
    verifier: str
    evaluation: str
    parameters: tuple[BenchmarkParameter, ...] = ()


@dataclass(slots=True)
class BenchmarkBundle:
    """A matched set of runtime components for one immutable Benchmark instance."""

    descriptor: BenchmarkDescriptor
    instance_id: str
    benchmark: Benchmark
    verifier: Verifier
    evaluation: Evaluation


class BenchmarkAdapter(Protocol):
    def create(
        self,
        descriptor: BenchmarkDescriptor,
        config: Mapping[str, str | int],
        content: FileContentStore,
    ) -> BenchmarkBundle: ...


class BenchmarkCatalog:
    def __init__(self, descriptors: tuple[BenchmarkDescriptor, ...]):
        self.descriptors = descriptors
        self._by_id = {item.benchmark_id: item for item in descriptors}
        if len(self._by_id) != len(descriptors):
            raise ValueError("SupportBenchmark 中存在重复 Benchmark ID")

    @classmethod
    def load(cls, path: Path = BUILTIN_MANIFEST) -> BenchmarkCatalog:
        raw = yaml.safe_load(path.read_text())
        root = _mapping(raw, "SupportBenchmark")
        _reject_unknown(root, {"schema_version", "benchmarks"}, "SupportBenchmark")
        if root.get("schema_version") != 1:
            raise ValueError("不支持的 SupportBenchmark schema_version")
        rows = root.get("benchmarks")
        if not isinstance(rows, list) or not rows:
            raise ValueError("SupportBenchmark benchmarks 必须为非空数组")
        return cls(tuple(_descriptor(row) for row in rows))

    def list(self) -> tuple[BenchmarkDescriptor, ...]:
        return self.descriptors

    def get(self, benchmark_id: str) -> BenchmarkDescriptor | None:
        return self._by_id.get(benchmark_id)


class BenchmarkRegistry:
    def __init__(
        self,
        catalog: BenchmarkCatalog,
        adapters: Mapping[str, BenchmarkAdapter],
        content: FileContentStore,
    ):
        self.catalog = catalog
        self.adapters = dict(adapters)
        self.content = content

    def register(
        self, adapter_name: str, adapter: BenchmarkAdapter, *, replace: bool = False
    ) -> None:
        """Register an externally owned adapter without changing the built-in catalog."""
        if not adapter_name.strip():
            raise ValueError("Benchmark Adapter 名称不能为空")
        if adapter_name in self.adapters and not replace:
            raise ValueError(f"Benchmark Adapter 已注册：{adapter_name}")
        self.adapters[adapter_name] = adapter

    def create(
        self,
        benchmark_id: str,
        config: Mapping[str, object] | None = None,
        *,
        environment: Mapping[str, str] | None = None,
    ) -> BenchmarkBundle:
        descriptor = self.catalog.get(benchmark_id)
        if descriptor is None:
            raise ValueError(f"未知 Benchmark：{benchmark_id}")
        adapter = self.adapters.get(descriptor.adapter)
        if adapter is None:
            raise ValueError(f"Benchmark Adapter 未注册：{descriptor.adapter}")
        supplied = dict(config or {})
        known = {parameter.name for parameter in descriptor.parameters}
        unknown = set(supplied) - known
        if unknown:
            raise ValueError(f"未知 Benchmark 配置：{sorted(unknown)}")
        environ = environment or {}
        resolved = {
            parameter.name: parameter.resolve(supplied.get(parameter.name, _MISSING), environ)
            for parameter in descriptor.parameters
        }
        return adapter.create(descriptor, resolved, self.content)


def create_builtin_registry(
    content: FileContentStore, path: Path = BUILTIN_MANIFEST
) -> BenchmarkRegistry:
    # Imports stay here so Catalog parsing remains independent of concrete providers.
    from areal_rsi.benchmarks.adapters import ArithmeticAdapter, Gsm8kAdapter

    return BenchmarkRegistry(
        BenchmarkCatalog.load(path),
        {"arithmetic-v1": ArithmeticAdapter(), "gsm8k-v1": Gsm8kAdapter()},
        content,
    )


def create_registry(
    content: FileContentStore,
    catalog: BenchmarkCatalog,
    adapters: Mapping[str, BenchmarkAdapter] | None = None,
) -> BenchmarkRegistry:
    """Assemble a registry from caller-owned catalog metadata and adapters.

    External Benchmarks should use this entry point instead of adding imports to
    ``create_builtin_registry`` or the default application manifest.
    """
    return BenchmarkRegistry(catalog, adapters or {}, content)


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} 必须为对象")
    return value


def _reject_unknown(value: Mapping[str, object], allowed: set[str], label: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"{label} 包含未知字段：{sorted(unknown)}")


def _required_text(value: Mapping[str, object], name: str, label: str) -> str:
    item = value.get(name)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{label}.{name} 必须为非空字符串")
    return item


def _descriptor(raw: object) -> BenchmarkDescriptor:
    value = _mapping(raw, "Benchmark")
    _reject_unknown(
        value,
        {
            "id",
            "version",
            "display_name",
            "description",
            "adapter",
            "data",
            "verifier",
            "evaluation",
            "parameters",
        },
        "Benchmark",
    )
    data = _mapping(value.get("data"), "Benchmark.data")
    verifier = _mapping(value.get("verifier"), "Benchmark.verifier")
    evaluation = _mapping(value.get("evaluation"), "Benchmark.evaluation")
    _reject_unknown(data, {"provider"}, "Benchmark.data")
    _reject_unknown(verifier, {"adapter"}, "Benchmark.verifier")
    _reject_unknown(evaluation, {"adapter"}, "Benchmark.evaluation")
    parameter_rows = _mapping(value.get("parameters", {}), "Benchmark.parameters")
    parameters = tuple(_parameter(name, row) for name, row in parameter_rows.items())
    return BenchmarkDescriptor(
        _required_text(value, "id", "Benchmark"),
        _required_text(value, "version", "Benchmark"),
        _required_text(value, "display_name", "Benchmark"),
        _required_text(value, "description", "Benchmark"),
        _required_text(value, "adapter", "Benchmark"),
        _required_text(data, "provider", "Benchmark.data"),
        _required_text(verifier, "adapter", "Benchmark.verifier"),
        _required_text(evaluation, "adapter", "Benchmark.evaluation"),
        parameters,
    )


def _parameter(name: str, raw: object) -> BenchmarkParameter:
    value = _mapping(raw, f"Benchmark.parameters.{name}")
    _reject_unknown(
        value,
        {"type", "env", "required", "default", "minimum"},
        f"Benchmark.parameters.{name}",
    )
    value_type = _required_text(value, "type", f"Benchmark.parameters.{name}")
    if value_type not in {"path", "string", "integer"}:
        raise ValueError(f"不支持的 Benchmark 参数类型：{value_type}")
    environment = value.get("env")
    required = value.get("required", False)
    minimum = value.get("minimum")
    default = value.get("default")
    if environment is not None and (not isinstance(environment, str) or not environment):
        raise ValueError(f"Benchmark.parameters.{name}.env 必须为字符串")
    if type(required) is not bool:
        raise ValueError(f"Benchmark.parameters.{name}.required 必须为布尔值")
    if minimum is not None and type(minimum) is not int:
        raise ValueError(f"Benchmark.parameters.{name}.minimum 必须为整数")
    if default is not None and not isinstance(default, (str, int)):
        raise ValueError(f"Benchmark.parameters.{name}.default 类型无效")
    parameter = BenchmarkParameter(name, value_type, environment, required, default, minimum)
    if default is not None:
        parameter.resolve(default, {})
    return parameter
