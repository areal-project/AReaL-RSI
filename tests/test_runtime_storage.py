"""Journal durability, single-writer ownership and failure boundaries."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from areal_agent_runtime.contracts.events import EventType, RunEvent
from areal_agent_runtime.contracts.product import RunSnapshot, RunStatus

from areal_rsi.modules.runtime_storage import JournalStateStore, read_journal


def initial() -> RunSnapshot:
    return RunSnapshot(
        run_id="run", session_id="session", profile="default",
        status=RunStatus.QUEUED, last_sequence=-1,
    )


def event(sequence: int, kind: EventType) -> RunEvent:
    return RunEvent(
        event_id=f"event-{sequence}", sequence=sequence, time=datetime.now(UTC),
        run_id="run", session_id="session", correlation_id="run", type=kind,
    )


def test_journal_appends_atomic_event_snapshot_and_rejects_invalid_commit(tmp_path: Path) -> None:
    async def check() -> None:
        state = JournalStateStore(tmp_path)
        try:
            snapshot = initial()
            await state.create_run(snapshot)
            prefix = (tmp_path / "journal.jsonl").read_bytes()
            queued = snapshot.model_copy(update={"last_sequence": 0})
            await state.commit_event(event(0, EventType.RUN_QUEUED), queued, outbox_sinks=())
            data = (tmp_path / "journal.jsonl").read_bytes()
            assert data.startswith(prefix) and len(data.splitlines()) == 2
            events, snapshots = read_journal(tmp_path)
            assert events == list(await state.list_events("run"))
            assert snapshots["run"] == queued
            with pytest.raises(ValueError):
                await state.commit_event(event(0, EventType.RUN_QUEUED), queued, outbox_sinks=())
            assert (tmp_path / "journal.jsonl").read_bytes() == data
            with pytest.raises(FileExistsError):
                JournalStateStore(tmp_path)
        finally:
            state.close()
        with pytest.raises(FileExistsError):
            JournalStateStore(tmp_path)  # New workers must use new session directories.

    asyncio.run(check())


def test_failed_fsync_is_not_published_and_writer_fails_closed(tmp_path: Path) -> None:
    async def check() -> None:
        state = JournalStateStore(tmp_path)
        try:
            snapshot = initial()
            await state.create_run(snapshot)
            data = (tmp_path / "journal.jsonl").read_bytes()
            queued = snapshot.model_copy(update={"last_sequence": 0})
            reader = state.stream_events("run")
            waiting = asyncio.ensure_future(anext(reader))
            await asyncio.sleep(0)
            with (
                patch("areal_rsi.modules.runtime_storage.os.fsync", side_effect=OSError("disk")),
                pytest.raises(OSError, match="disk"),
            ):
                await state.commit_event(event(0, EventType.RUN_QUEUED), queued, outbox_sinks=())
            with pytest.raises(RuntimeError, match="journal failed"):
                await waiting
            with pytest.raises(RuntimeError):
                await state.list_events("run")
            with pytest.raises(RuntimeError, match="writer is unavailable"):
                await state.commit_event(event(0, EventType.RUN_QUEUED), queued, outbox_sinks=())
            assert (tmp_path / "journal.jsonl").read_bytes() == data
            events, snapshots = read_journal(tmp_path)
            assert not events and snapshots["run"] == snapshot
        finally:
            state.close()

    asyncio.run(check())


def test_crash_tail_is_ignored_but_complete_corruption_is_rejected(tmp_path: Path) -> None:
    async def write() -> None:
        state = JournalStateStore(tmp_path)
        try:
            await state.create_run(initial())
        finally:
            state.close()

    asyncio.run(write())
    path = tmp_path / "journal.jsonl"
    data = path.read_bytes()
    with path.open("ab") as stream:
        stream.write(b'{"version": "unfinished')
    assert read_journal(tmp_path) == ([], {"run": initial()})
    path.write_bytes(data + b'not-json\n')
    with pytest.raises(ValueError):
        read_journal(tmp_path)
