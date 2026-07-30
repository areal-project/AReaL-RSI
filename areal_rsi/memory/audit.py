"""Append-only audit logging for controller actions."""

from __future__ import annotations

import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .ledger import append_ledger

SECRET_KEY_PARTS = ("api_key", "apikey", "auth", "authorization", "password", "secret", "token")


class AuditLogger:
    """Records action input/output/error events for a single run."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._action_index = 0

    @contextmanager
    def action(
        self,
        *,
        run_id: str,
        iteration: int,
        action_type: str,
        step: str,
        input: dict[str, Any] | None = None,
        actor: str = "RSIController",
        metadata: dict[str, Any] | None = None,
    ) -> Iterator["AuditAction"]:
        """Record start and terminal events around one controller action."""

        action = AuditAction(
            logger=self,
            action_id=self._new_action_id(),
            run_id=run_id,
            iteration=iteration,
            action_type=action_type,
            step=step,
            actor=actor,
            started_at=_utc_now(),
            input=input or {},
            metadata=metadata or {},
        )
        self._append(action.event("started"))
        try:
            yield action
        except Exception as exc:
            action.fail(exc)
            raise
        else:
            action.succeed()

    def _new_action_id(self) -> str:
        self._action_index += 1
        return f"act_{self._action_index:03d}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"

    def _append(self, event: dict[str, Any]) -> None:
        append_ledger(self.path, _redact(event))


class AuditAction:
    """Mutable action context populated by controller code before exit."""

    def __init__(
        self,
        *,
        logger: AuditLogger,
        action_id: str,
        run_id: str,
        iteration: int,
        action_type: str,
        step: str,
        actor: str,
        started_at: str,
        input: dict[str, Any],
        metadata: dict[str, Any],
    ):
        self.logger = logger
        self.action_id = action_id
        self.run_id = run_id
        self.iteration = iteration
        self.action_type = action_type
        self.step = step
        self.actor = actor
        self.started_at = started_at
        self.input = input
        self.metadata = metadata
        self.output: dict[str, Any] = {}
        self.artifacts: dict[str, str] = {}

    def set_output(
        self,
        output: dict[str, Any] | None = None,
        *,
        artifacts: dict[str, str | Path] | None = None,
    ) -> None:
        self.output = output or {}
        self.artifacts = {key: str(value) for key, value in (artifacts or {}).items()}

    def event(self, status: str, *, error: dict[str, Any] | None = None) -> dict[str, Any]:
        ended_at = _utc_now() if status != "started" else None
        return {
            "schema_version": "audit.v1",
            "event_id": f"evt_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}",
            "run_id": self.run_id,
            "iteration": self.iteration,
            "action_id": self.action_id,
            "action_type": self.action_type,
            "step": self.step,
            "status": status,
            "actor": self.actor,
            "started_at": self.started_at,
            "ended_at": ended_at,
            "input": self.input,
            "output": self.output if status == "succeeded" else {},
            "artifacts": self.artifacts,
            "metadata": self.metadata,
            "error": error,
        }

    def succeed(self) -> None:
        self.logger._append(self.event("succeeded"))

    def fail(self, exc: Exception) -> None:
        self.logger._append(
            self.event(
                "failed",
                error={
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "traceback": traceback.format_exc(),
                },
            )
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if _is_secret_key(str(key)):
                redacted[key] = "***REDACTED***"
            else:
                redacted[key] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SECRET_KEY_PARTS)
