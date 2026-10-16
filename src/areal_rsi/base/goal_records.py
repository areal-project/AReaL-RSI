"""Read-only projections for current execution versus previous Goal outcomes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from areal_rsi.base.storage import safe_name


def optional_object(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("Goal state record must be an object")
    return value


def execution_projection(
    root: Path, key: str, state: str, run_id: str | None, active: bool
) -> dict[str, Any]:
    key = safe_name(key)
    outcome = optional_object(root / "goal-outcomes" / f"{key}.json")
    current = outcome if outcome and outcome.get("state") == state else None
    ledger = optional_object(root / "goal-workers" / f"{key}-ledger.json")
    progress = (
        optional_object(root / "runs" / safe_name(run_id) / "progress.json") if run_id else None
    )
    retry_history = []
    for path in sorted((root / "goal-events" / key).glob("*.json")):
        event = optional_object(path)
        if event and event.get("type") in {"attempt_failed", "retry"}:
            retry_history.append(
                {
                    "type": event["type"],
                    "at": event.get("at"),
                    "attempt": event.get("attempt"),
                    "retry": event.get("retry"),
                    "error": event.get("error"),
                    "detail_available": event.get("type") == "attempt_failed",
                }
            )
    return {
        "retry_history": retry_history,
        "worker_active": active,
        "outcome": current,
        "previous_outcome": outcome if current is None else None,
        "resource_usage": ledger,
        "progress": progress,
        "recovery_required": state == "running" and not active,
    }
