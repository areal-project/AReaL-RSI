"""Explicit external assembly for the local 3D Benchmark example."""

from __future__ import annotations

import os
from pathlib import Path

from adapter import ThreeModelAdapter

from areal_rsi.base.storage import FileContentStore
from areal_rsi.benchmarks import BenchmarkCatalog, create_registry


def create_3d_registry(content: FileContentStore, *, tasks_path: str, runtime_path: str):
    """Build the 3D registry without modifying AReaL-RSI's built-in registry."""
    catalog = BenchmarkCatalog.load(Path(__file__).with_name("benchmark.yaml"))
    registry = create_registry(content, catalog, {"3d-model-v1": ThreeModelAdapter()})
    return registry, {
        "tasks_path": tasks_path,
        "runtime_path": runtime_path,
    }


def create_3d_registry_from_environment(content: FileContentStore):
    tasks_path = os.environ.get("RSI_3D_TASKS_PATH")
    runtime_path = os.environ.get("RSI_3D_RUNTIME_PATH")
    if not tasks_path or not runtime_path:
        raise ValueError("需要 RSI_3D_TASKS_PATH 和 RSI_3D_RUNTIME_PATH")
    return create_3d_registry(content, tasks_path=tasks_path, runtime_path=runtime_path)
