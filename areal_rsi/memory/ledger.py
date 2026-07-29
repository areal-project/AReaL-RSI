"""JSONL ledger utilities."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def append_ledger(path: str | Path, event: dict[str, Any]) -> None:
    ledger_path = Path(path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, default=repr) + "\n")


def read_ledger(path: str | Path, limit: int | None = None) -> list[dict[str, Any]]:
    ledger_path = Path(path)
    if not ledger_path.exists():
        return []

    rows: list[dict[str, Any]] = []
    with ledger_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows[-limit:] if limit else rows
