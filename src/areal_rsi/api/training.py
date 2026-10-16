"""Asynchronous model-training lifecycle; no real backend is bundled."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from areal_rsi.api.base import (
    ContentRef,
    JsonObject,
    JsonValue,  # noqa: F401
    ModelChange,
    ModelVersion,
)


class TrainingState(StrEnum):
    SUBMITTED = "submitted"
    RUNNING = "running"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    FAILED = "failed"
    COMPLETED = "completed"


@dataclass(slots=True)
class TrainingRequest:
    execution_key: str
    parent: ModelVersion
    change: ModelChange
    resources: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class TrainingJob:
    job_id: str
    request: TrainingRequest
    state: TrainingState
    checkpoint: ContentRef | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if not self.request.execution_key.strip():
            raise ValueError("Training submission requires an idempotency key")
        if self.state == TrainingState.COMPLETED and self.checkpoint is None:
            raise ValueError("Completed training requires a real checkpoint reference")


class ManagedTrainingBackend(Protocol):
    async def submit(self, request: TrainingRequest) -> TrainingJob: ...

    async def status(self, job_id: str) -> TrainingJob: ...

    async def cancel(self, job_id: str) -> TrainingJob: ...

    async def resume(self, job_id: str, *, checkpoint: ContentRef | None = None) -> TrainingJob: ...
