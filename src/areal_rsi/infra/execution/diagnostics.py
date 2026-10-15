"""Optional bounded, operator-published failure explanations; never raw verifier logs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_diagnostic(output: Path) -> dict[str, Any] | None:
    path = output / "diagnostic.json"
    try:
        if path.is_symlink() or not path.is_file():
            return None
        with path.open("rb") as stream:
            raw = stream.read(16001)
        if len(raw) > 16000:
            return None
        value = json.loads(raw)
        if not isinstance(value, dict):
            return None
        public = {
            key: value[key][:limit]
            for key, limit in (("code", 120), ("stage", 120), ("message", 2000), ("action", 1000))
            if isinstance(value.get(key), str)
        }
        if not public.get("message"):
            return None
        return public
    except (OSError, ValueError, RecursionError):
        return None


def failure_summary(output: Path, fallback: str) -> str:
    diagnostic = read_diagnostic(output)
    if not diagnostic:
        return fallback
    return " · ".join(
        diagnostic[key] for key in ("code", "message", "action") if diagnostic.get(key)
    )
