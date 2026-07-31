"""Persistent run-state schema."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Self

from .workflow import pending_steps

RunStatus = Literal["created", "running", "waiting_for_acceptance", "completed", "failed", "stopped"]


@dataclass(slots=True)
class RunState:
    """Source of truth for a single RSI workflow run."""

    run_id: str
    ckpt: str
    iteration: int = 0
    status: RunStatus = "created"
    current_step: str | None = None
    model_ref: dict[str, Any] | None = None
    eval_job: dict[str, Any] | None = None
    train_run: dict[str, Any] | None = None
    holdout_job: dict[str, Any] | None = None
    completed_steps: list[str] = field(default_factory=list)
    pending_steps: list[str] = field(default_factory=list)
    error: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.pending_steps:
            self.pending_steps = pending_steps(self.completed_steps)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(**data)

    @classmethod
    def load(cls, path: str | Path) -> Self:
        state_path = Path(path)
        return cls.from_dict(json.loads(state_path.read_text(encoding="utf-8")))

    def save(self, path: str | Path) -> None:
        state_path = Path(path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False, default=repr),
            encoding="utf-8",
        )

    def mark_step(self, step: str, *, status: RunStatus = "running", **updates: Any) -> None:
        self.current_step = step
        self.status = status
        if step not in self.completed_steps:
            self.completed_steps.append(step)
        self.pending_steps = pending_steps(self.completed_steps)
        for key, value in updates.items():
            if not hasattr(self, key):
                raise ValueError(f"Unknown RunState field: {key}")
            setattr(self, key, value)

    def mark_failed(self, error: dict[str, Any]) -> None:
        self.status = "failed"
        self.error = error

    def mark_stopped(self, reason: str | None = None) -> None:
        self.status = "stopped"
        self.error = {"reason": reason} if reason else None
