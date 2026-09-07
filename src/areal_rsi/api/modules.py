"""Minimal module interfaces used by RSI workflows."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from areal_rsi.api.base import (
    AgentSpec,
    ArtifactChange,
    ArtifactVersion,
    BenchmarkSplit,
    ContentRef,
    EvaluationReport,
    HarnessChange,
    HarnessVersion,
    JsonObject,
    JsonValue,
    MemoryHit,
    MemoryRecord,
    ModelChange,
    ModelVersion,
    SolutionVersion,
    Task,
    TaskResult,
    Trajectory,
    TrajectoryRef,
    VerificationResult,
)


class ContentStore(Protocol):
    async def put_bytes(self, content: bytes, *, media_type: str | None = None) -> ContentRef: ...

    async def put_text(
        self,
        content: str,
        *,
        media_type: str = "text/plain; charset=utf-8",
    ) -> ContentRef: ...

    async def put_json(self, content: JsonValue) -> ContentRef: ...

    async def read_bytes(self, ref: ContentRef) -> bytes: ...

    async def read_text(self, ref: ContentRef) -> str: ...

    async def read_json(self, ref: ContentRef) -> JsonValue: ...

    async def resolve_path(self, ref: ContentRef) -> Path: ...


class Memory(Protocol):
    async def add(
        self,
        text: str,
        *,
        source: str,
        tags: Sequence[str] = (),
    ) -> MemoryRecord: ...

    async def get(self, memory_id: str) -> MemoryRecord | None: ...

    async def search(self, query: str, *, limit: int = 10) -> Sequence[MemoryHit]: ...


class TrajectoryStore(Protocol):
    """Runtime saves each completed trajectory exactly once."""

    async def save(self, trajectory: Trajectory) -> TrajectoryRef: ...

    async def get(self, trajectory_id: str) -> Trajectory | None: ...

    async def bundle(self, trajectories: Sequence[TrajectoryRef]) -> ContentRef:
        """Create a standard trajectory manifest ContentRef."""
        ...


class VersionManager(Protocol):
    """Create and retrieve append-only versions; Run owns current/best state."""

    async def create_model(
        self,
        name: str,
        content: ContentRef,
        *,
        parent_version_id: str | None = None,
    ) -> ModelVersion: ...

    async def create_harness(
        self,
        name: str,
        content: ContentRef,
        *,
        parent_version_id: str | None = None,
    ) -> HarnessVersion: ...

    async def create_artifact(
        self,
        name: str,
        content: ContentRef,
        *,
        parent_version_id: str | None = None,
    ) -> ArtifactVersion: ...

    async def create_solution(
        self,
        model: ModelVersion,
        harness: HarnessVersion,
        *,
        artifacts: Sequence[ArtifactVersion] = (),
        parent_solution_id: str | None = None,
    ) -> SolutionVersion: ...

    async def get_model(self, version_id: str) -> ModelVersion | None: ...

    async def get_harness(self, version_id: str) -> HarnessVersion | None: ...

    async def get_artifact(self, version_id: str) -> ArtifactVersion | None: ...

    async def get_solution(self, solution_id: str) -> SolutionVersion | None: ...

    async def list_child_solutions(self, solution_id: str) -> Sequence[SolutionVersion]: ...


class AgentSession(Protocol):
    """One multi-turn Agent conversation."""

    async def send(self, message: str) -> str: ...

    async def finish(self) -> TrajectoryRef: ...


class AgentRuntime(Protocol):
    async def open(
        self,
        agent: AgentSpec,
        *,
        workspace_root: str | None = None,
    ) -> AgentSession: ...


class HarnessRuntime(Protocol):
    """Run one public Task with the user's Harness."""

    async def run(
        self,
        solution: SolutionVersion,
        task: Task,
        *,
        config: JsonObject | None = None,
    ) -> TaskResult: ...


class HarnessBuilder(Protocol):
    async def build(self, base: HarnessVersion, change: HarnessChange) -> ContentRef: ...


class ArtifactBuilder(Protocol):
    async def build(
        self,
        base: ArtifactVersion | None,
        change: ArtifactChange,
    ) -> ContentRef: ...


class TrainingBackend(Protocol):
    """Backend-neutral model training interface; AReaL is one implementation."""

    async def train(self, base: ModelVersion, change: ModelChange) -> ContentRef: ...


class Benchmark(Protocol):
    """Expose public inputs only; hidden expected data stays in Verifier."""

    @property
    def name(self) -> str: ...

    async def list_tasks(self, split: BenchmarkSplit) -> Sequence[Task]: ...


class Verifier(Protocol):
    """Load hidden expected data internally and verify one Task result."""

    async def verify(
        self,
        task: Task,
        solution: SolutionVersion,
        result: TaskResult,
    ) -> VerificationResult: ...


class Evaluation(Protocol):
    async def evaluate(
        self,
        solution: SolutionVersion,
        split: BenchmarkSplit,
        results: Sequence[VerificationResult],
        *,
        baseline: EvaluationReport | None = None,
    ) -> EvaluationReport: ...
