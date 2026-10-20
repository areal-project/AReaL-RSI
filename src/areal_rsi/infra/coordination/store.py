"""Atomic session records and a small UI transcript; native history belongs to Codex."""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import TextIO

from areal_rsi.api.control import decode_record, encode_record, public_tool_text
from areal_rsi.api.research import EventPage, SessionEvent, SessionRecord


class SessionStore:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def directory(self, run_id: str) -> Path:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", run_id):
            raise ValueError("Invalid run ID")
        return self.root / "sessions" / run_id

    def acquire(self, run_id: str) -> TextIO:
        directory = self.directory(run_id)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        handle = (directory / "owner.lock").open("a+")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            raise ValueError("This conversation is already open in another RSI process") from None
        return handle

    def save(self, record: SessionRecord) -> None:
        directory = self.directory(record.run_id)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        content = json.dumps(encode_record(record), ensure_ascii=False, indent=2)
        fd, name = tempfile.mkstemp(prefix=".state-", dir=directory)
        try:
            with os.fdopen(fd, "w") as output:
                output.write(content + "\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(name, directory / "session.json")
        finally:
            Path(name).unlink(missing_ok=True)

    def load(self, run_id: str) -> SessionRecord:
        path = self.directory(run_id) / "session.json"
        if not path.exists():
            raise ValueError(f"Unknown conversation: {run_id}")
        return decode_record(SessionRecord, json.loads(path.read_text()))

    def list_sessions(self) -> list[SessionRecord]:
        paths = sorted(
            (self.root / "sessions").glob("*/session.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return [self.load(path.parent.name) for path in paths]

    def append(self, run_id: str, event: SessionEvent) -> None:
        if event.kind in {"delta", "status", "prompt"}:
            return
        path = self.directory(run_id) / "transcript.jsonl"
        with path.open("a") as output:
            output.write(json.dumps(encode_record(event), ensure_ascii=False) + "\n")
        path.chmod(0o600)

    def transcript(self, run_id: str) -> list[SessionEvent]:
        path = self.directory(run_id) / "transcript.jsonl"
        if not path.exists():
            return []
        return [
            decode_record(SessionEvent, json.loads(line)) for line in path.read_text().splitlines()
        ]

    def read_events(
        self, run_id: str, cursor: int = 0, *, include_tools: bool = False
    ) -> EventPage:
        """Replay a bounded page; each observer owns its independent byte cursor."""
        path = self.directory(run_id) / "transcript.jsonl"
        size = path.stat().st_size if path.exists() else 0
        if cursor < 0 or cursor > size:
            raise ValueError("Invalid event cursor")
        if not size:
            return EventPage(0, [])
        events: list[SessionEvent] = []
        with path.open("rb") as source:
            if cursor:
                source.seek(cursor - 1)
                if source.read(1) != b"\n":
                    raise ValueError("Event cursor must be a record boundary")
            source.seek(cursor)
            for _ in range(100):
                line = source.readline()
                if not line:
                    break
                event = decode_record(SessionEvent, json.loads(line))
                if include_tools or event.kind != "tool":
                    events.append(replace(event, text=public_tool_text(event.text, 3000)))
                if len(events) == 16:
                    break
            return EventPage(source.tell(), events, source.tell() < size)
