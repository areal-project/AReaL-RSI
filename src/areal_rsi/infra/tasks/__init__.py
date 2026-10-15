"""Benchmark discovery and trusted adapter assembly."""

from areal_rsi.infra.tasks.catalog import (
    BenchmarkBundle,
    BenchmarkCatalog,
    BenchmarkDescriptor,
    BenchmarkRegistry,
    create_builtin_registry,
    create_registry,
)

__all__ = [
    "BenchmarkBundle",
    "BenchmarkCatalog",
    "BenchmarkDescriptor",
    "BenchmarkRegistry",
    "create_builtin_registry",
    "create_registry",
]
