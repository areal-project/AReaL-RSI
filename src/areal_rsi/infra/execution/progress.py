"""Optional public job progress, independent of evaluation results and agent state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MAX_PROGRESS_BYTES = 128_000


def public_progress(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, Any] = {}
    for name, limit in (("message", 2000), ("stage", 160), ("unit", 40), ("updated_at", 80)):
        if isinstance(value.get(name), str):
            result[name] = value[name][:limit]
    completed, total = value.get("completed"), value.get("total")
    if (
        isinstance(completed, int | float)
        and isinstance(total, int | float)
        and not isinstance(completed, bool)
        and not isinstance(total, bool)
        and 0 <= completed <= total <= 2**53 - 1
        and total > 0
    ):
        result.update(completed=completed, total=total)
    items = value.get("items")
    if isinstance(items, list):
        result["items"] = [
            {
                key: item[key][:limit]
                for key, limit in (("id", 160), ("label", 200), ("status", 40), ("message", 600))
                if isinstance(item.get(key), str)
            }
            for item in items[:100]
            if isinstance(item, dict)
        ]
    return result or None


def read_progress(output: Path) -> dict[str, Any] | None:
    """Malformed, oversized or partial writes never prevent viewing a run."""
    path = output / "progress.json"
    try:
        if path.is_symlink() or not path.is_file():
            return None
        with path.open("rb") as handle:
            raw = handle.read(MAX_PROGRESS_BYTES + 1)
        if len(raw) > MAX_PROGRESS_BYTES:
            return None
        return public_progress(json.loads(raw))
    except (OSError, ValueError, RecursionError):
        return None
