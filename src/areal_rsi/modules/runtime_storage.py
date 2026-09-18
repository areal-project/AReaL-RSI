"""Single-worker Runtime state with an append-only, fsynced JSONL journal.

In-process queries use Runtime's memory store. A restarted worker replays the
validated durable journal through the same public StateStore operations.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path
from typing import Any

from areal_agent_runtime.adapters.storage import FileSystemAssetStore
from areal_agent_runtime.contracts.environment import AgentEnvironmentSnapshot
from areal_agent_runtime.contracts.events import RunEvent
from areal_agent_runtime.contracts.product import RunSnapshot
from areal_agent_runtime.kernel import InMemoryStateStore
from areal_agent_runtime.plugin_sdk import (
    HealthStatus,
    PluginContext,
    PluginEntrypoints,
    PluginManifest,
    PortKey,
)
from pydantic import JsonValue

JOURNAL_VERSION = "rsi-runtime-journal-v1"


class JournalStateStore(InMemoryStateStore):
    """Publish to readers only after the corresponding record reaches disk.

    Runtime validates state transitions in super(). The small synchronous append
    between that validation and returning deliberately has no await: another task
    cannot observe the memory update before fsync. Any write failure poisons this
    worker's writer; the parent exports only the surviving durable journal prefix.
    Leases are process-local because a journal has exactly one worker owner.
    """

    def __init__(self, root: Path, *, resume: bool = False):
        super().__init__()
        root.mkdir(parents=True, exist_ok=True)
        self.stream = (root / "journal.jsonl").open("a+b" if resume else "xb", buffering=0)
        try:
            fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self.stream.close()
            raise
        self.write_lock = asyncio.Lock()
        self.failed = False
        directory = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    async def restore(self) -> None:
        self.stream.seek(0)
        while True:
            offset = self.stream.tell()
            line = self.stream.readline()
            if not line:
                break
            if not line.endswith(b"\n"):
                self.stream.truncate(offset)
                os.fsync(self.stream.fileno())
                break
            record = json.loads(line)
            if record["version"] != JOURNAL_VERSION:
                raise ValueError("Unsupported Runtime journal version")
            operation = record["operation"]
            if operation == "create":
                await super().create_run(RunSnapshot.model_validate(record["snapshot"]))
            elif operation == "commit":
                await super().commit_event(
                    RunEvent.model_validate(record["event"]),
                    RunSnapshot.model_validate(record["snapshot"]),
                    outbox_sinks=(),
                )
            elif operation == "environment":
                await super().save_environment_snapshot(
                    record["session_id"],
                    AgentEnvironmentSnapshot.model_validate(record["snapshot"]),
                )
            else:
                raise ValueError("Unknown Runtime journal operation")
        self.stream.seek(0, os.SEEK_END)

    def _check_writer(self) -> None:
        if self.failed or self.stream.closed:
            raise RuntimeError("Runtime journal writer is unavailable")

    def _check_reader(self) -> None:
        if self.failed:
            raise RuntimeError("Runtime journal failed; read its durable prefix after worker exit")

    async def get_run(self, run_id: str) -> RunSnapshot | None:
        self._check_reader()
        return await super().get_run(run_id)

    async def list_events(self, run_id: str) -> tuple[RunEvent, ...]:
        self._check_reader()
        return await super().list_events(run_id)

    async def latest_event_sequence(self, run_id: str) -> int:
        self._check_reader()
        return await super().latest_event_sequence(run_id)

    async def get_environment_snapshot(self, session_id: str) -> AgentEnvironmentSnapshot | None:
        self._check_reader()
        return await super().get_environment_snapshot(session_id)

    def stream_events(self, run_id: str, after: int | None = None) -> AsyncIterator[RunEvent]:
        return self._durable_events(run_id, after)

    async def _durable_events(self, run_id: str, after: int | None) -> AsyncIterator[RunEvent]:
        self._check_reader()
        async for event in super().stream_events(run_id, after):
            self._check_reader()
            yield event

    def _append(self, operation: str, **data: Any) -> None:
        start = self.stream.tell()
        try:
            payload = (
                json.dumps(
                    {"version": JOURNAL_VERSION, "operation": operation, **data},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode()
                + b"\n"
            )
            remaining = memoryview(payload)
            while remaining:
                written = self.stream.write(remaining)
                if not written:
                    raise OSError("Runtime journal write made no progress")
                remaining = remaining[written:]
            os.fsync(self.stream.fileno())
        except BaseException:
            self.failed = True
            with suppress(OSError):
                self.stream.truncate(start)
                os.fsync(self.stream.fileno())
            raise

    async def create_run(self, snapshot: RunSnapshot) -> None:
        async with self.write_lock:
            self._check_writer()
            await super().create_run(snapshot)
            self._append("create", snapshot=snapshot.model_dump(mode="json", by_alias=True))

    async def commit_event(
        self, event: RunEvent, snapshot: RunSnapshot, *, outbox_sinks: tuple[str, ...]
    ) -> None:
        async with self.write_lock:
            self._check_writer()
            if outbox_sinks:
                raise ValueError("RSI exports its journal directly; event sinks are not supported")
            await super().commit_event(event, snapshot, outbox_sinks=())
            self._append(
                "commit",
                event=event.model_dump(mode="json", by_alias=True),
                snapshot=snapshot.model_dump(mode="json", by_alias=True),
            )

    async def save_environment_snapshot(
        self, session_id: str, snapshot: AgentEnvironmentSnapshot
    ) -> None:
        async with self.write_lock:
            self._check_writer()
            await super().save_environment_snapshot(session_id, snapshot)
            self._append(
                "environment",
                session_id=session_id,
                snapshot=snapshot.model_dump(mode="json", by_alias=True),
            )

    def close(self) -> None:
        self.stream.close()


def read_journal(root: Path) -> tuple[list[RunEvent], dict[str, RunSnapshot]]:
    """Read a stopped worker; ignore only an incomplete final write after a crash."""
    events: list[RunEvent] = []
    snapshots: dict[str, RunSnapshot] = {}
    path = root / "journal.jsonl"
    if not path.exists():
        return events, snapshots  # Bootstrap can fail before storage is configured.
    with path.open("rb") as stream:
        for line in stream:
            if not line.endswith(b"\n"):
                break
            record = json.loads(line)
            if record["version"] != JOURNAL_VERSION:
                raise ValueError("Unsupported Runtime journal version")
            operation = record["operation"]
            if operation == "environment":
                continue
            snapshot = RunSnapshot.model_validate(record["snapshot"])
            current = snapshots.get(snapshot.run_id)
            if operation == "create":
                if current is not None or snapshot.last_sequence != -1:
                    raise ValueError("Invalid Runtime journal run creation")
            elif operation == "commit":
                event = RunEvent.model_validate(record["event"])
                if (
                    current is None
                    or event.run_id != snapshot.run_id
                    or event.session_id != snapshot.session_id
                    or event.sequence != current.last_sequence + 1
                    or snapshot.last_sequence != event.sequence
                ):
                    raise ValueError("Invalid Runtime journal event sequence or identity")
                events.append(event)
            else:
                raise ValueError("Unknown Runtime journal operation")
            snapshots[snapshot.run_id] = snapshot
    return events, snapshots


class JournalStoragePlugin:
    manifest = PluginManifest(
        name="rsi-journal-storage",
        version="1.0.0",
        implements=(PortKey.STATE_STORE, PortKey.ASSET_STORE),
        entrypoints=PluginEntrypoints(local="areal_rsi.modules.runtime_storage:create_plugin"),
        config_schema="runtime_storage.schema.json",
        permissions=("filesystem.read", "filesystem.write"),
    )

    def __init__(self) -> None:
        self.state: JournalStateStore | None = None

    async def configure(self, context: PluginContext, config: dict[str, JsonValue]) -> None:
        root = config.get("root")
        if not isinstance(root, str) or not root:
            raise ValueError("Runtime storage requires a root directory")
        self.state = JournalStateStore(Path(root) / "state", resume=True)
        await self.state.restore()
        context.provide(PortKey.STATE_STORE, self.state)
        context.provide(PortKey.ASSET_STORE, FileSystemAssetStore(Path(root) / "assets"))

    async def start(self) -> None:
        if self.state is None:
            raise RuntimeError("Runtime storage is not configured")

    async def health(self) -> HealthStatus:
        healthy = self.state is not None and not self.state.failed and not self.state.stream.closed
        return HealthStatus(status="healthy" if healthy else "unhealthy")

    async def stop(self) -> None:
        if self.state is not None:
            self.state.close()


def create_plugin() -> JournalStoragePlugin:
    return JournalStoragePlugin()
