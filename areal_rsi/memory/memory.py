"""Memory interface and a simple file-backed implementation."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .ledger import append_ledger, read_ledger


class Memory:
    """Records RSI events and exposes compact context for agents."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.ledger_path = self.root / "ledger.jsonl"
        self.insights_path = self.root / "insights.md"

    def record(self, title: str, content: str, metadata: dict[str, Any] | None = None) -> str:
        event_id = f"evt_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        event = {
            "event_id": event_id,
            "time": datetime.now(timezone.utc).isoformat(),
            "title": title,
            "content": content,
            "metadata": metadata or {},
        }
        self.append_ledger(event)
        return event_id

    def get_context(
        self,
        ckpt: str | None = None,
        run_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        events = read_ledger(self.ledger_path, limit=limit)
        if ckpt is not None:
            events = [event for event in events if event.get("metadata", {}).get("ckpt") == ckpt]
        if run_id is not None:
            events = [event for event in events if event.get("metadata", {}).get("run_id") == run_id]
        insights = self.insights_path.read_text(encoding="utf-8") if self.insights_path.exists() else ""
        return {"events": events, "insights": insights}

    def append_ledger(self, event: dict[str, Any]) -> None:
        append_ledger(self.ledger_path, event)
