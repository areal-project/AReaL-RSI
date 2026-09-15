"""Remote task execution providers, independent of research orchestration."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from areal_rsi.api.base import JsonObject, SolutionVersion, VerifierSpec
from areal_rsi.api.modules import (
    Benchmark,
    Evaluation,
    HarnessBuilder,
    HarnessRuntime,
    Verifier,
)
from areal_rsi.base.runs import RunPaths
from areal_rsi.base.storage import (
    FileContentStore,
    FileMemory,
    FileTrajectoryStore,
    FileVersionManager,
)


@dataclass(frozen=True)
class RemoteContext:
    root: Path
    content: FileContentStore
    memory: FileMemory
    trajectories: FileTrajectoryStore
    versions: FileVersionManager
    emit: Callable[[str], None]


@dataclass(frozen=True)
class RemoteBackend:
    baseline: SolutionVersion
    benchmark: Benchmark
    harness_runtime: HarnessRuntime
    verifier: Verifier
    verifier_spec: VerifierSpec
    evaluation: Evaluation
    harness_builder: HarnessBuilder
    description: str
    identity: JsonObject = field(default_factory=dict)
    bind_run: Callable[[RunPaths], None] | None = None


@dataclass(frozen=True)
class RemoteJob:
    """Public job projection; no credentials, request bodies or trajectories."""

    job_id: str | None
    state: str
    task_counts: dict[str, int]
    url: str | None = None

    def __post_init__(self) -> None:
        if self.url is not None:
            parsed = urlsplit(self.url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or (parsed.username or parsed.password)
            ):
                raise ValueError("Remote Job URL 必须是无凭证的 HTTP(S) 地址")


class RemoteProvider(Protocol):
    @property
    def environment_names(self) -> tuple[str, ...]: ...

    async def create(self, context: RemoteContext, config_path: Path) -> RemoteBackend: ...

    def read_jobs(self, root: Path, run_id: str) -> Sequence[RemoteJob]:
        """Read local status only, without connecting to a backend or starting Agents."""
        ...
