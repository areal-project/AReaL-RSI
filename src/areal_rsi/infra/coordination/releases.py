"""Content-addressed release records and an atomic active pointer."""

from __future__ import annotations

import fcntl
import hashlib
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

from areal_rsi.api.control import json_bytes, read_record, safe_name
from areal_rsi.api.research import ReleaseRecord, RollbackEvent
from areal_rsi.infra.storage.files import write_bytes


@dataclass(frozen=True, slots=True)
class ActiveRelease:
    release_id: str | None

    def __post_init__(self) -> None:
        if self.release_id is not None and not re.fullmatch(
            r"release_[0-9a-f]{64}", self.release_id
        ):
            raise ValueError("Active release pointer is invalid")


@contextmanager
def _release_lock(root: Path) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".release.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def active_release(root: Path) -> ReleaseRecord | None:
    pointer = root / "active.json"
    if not pointer.exists():
        return None
    release_id = read_record(pointer, ActiveRelease).release_id
    if release_id is None:
        return None
    release = read_record(root / "releases" / f"{release_id}.json", ReleaseRecord)
    _validate_release_identity(release, release_id)
    return release


def _validate_release_identity(release: ReleaseRecord, expected: str) -> None:
    provisional = replace(release, release_id="release_pending")
    identity = "release_" + hashlib.sha256(json_bytes(provisional)).hexdigest()
    if release.release_id != expected or identity != expected:
        raise ValueError("Published release content differs from its identity")


def release_ancestry(root: Path, current: ReleaseRecord) -> tuple[ReleaseRecord, ...]:
    seen: set[str] = set()
    records = []
    node: ReleaseRecord | None = current
    while node is not None:
        if node.release_id in seen:
            raise ValueError("Release ancestry contains a cycle")
        seen.add(node.release_id)
        records.append(node)
        if node.parent_release_id is None:
            node = None
        else:
            expected = node.parent_release_id
            node = read_record(root / "releases" / f"{expected}.json", ReleaseRecord)
            _validate_release_identity(node, expected)
    return tuple(records)


def publish(root: Path, release: ReleaseRecord) -> ReleaseRecord:
    with _release_lock(root):
        return _publish_locked(root, release)


def _publish_locked(root: Path, release: ReleaseRecord) -> ReleaseRecord:
    provisional = replace(release, release_id="release_pending")
    identity = "release_" + hashlib.sha256(json_bytes(provisional)).hexdigest()
    final = replace(release, release_id=identity)
    current = active_release(root)
    expected = release.parent_release_id
    if current is None and expected is not None:
        raise ValueError("Parent release is absent")
    if current is not None and current.release_id not in {expected, identity}:
        raise ValueError("Active release changed since generation started")
    if current is not None and current.release_id == identity:
        if current != final:
            raise ValueError("Release identity collision")
        return current
    write_bytes(root / "releases" / f"{identity}.json", json_bytes(final), immutable=True)
    write_bytes(root / "active.json", json_bytes(ActiveRelease(identity)))
    return final


def rollback(root: Path, run_id: str, expected_release_id: str, reason: str) -> RollbackEvent:
    with _release_lock(root):
        return _rollback_locked(root, run_id, expected_release_id, reason)


def _rollback_locked(
    root: Path, run_id: str, expected_release_id: str, reason: str
) -> RollbackEvent:
    safe_name(run_id)
    if not re.fullmatch(r"release_[0-9a-f]{64}", expected_release_id):
        raise ValueError("Expected release ID is invalid")
    if not reason.strip() or len(reason) > 1000:
        raise ValueError("Rollback needs a concise audit reason")
    release = read_record(root / "releases" / f"{expected_release_id}.json", ReleaseRecord)
    _validate_release_identity(release, expected_release_id)
    if release.run_id != run_id:
        raise ValueError("Rollback run ID does not own the expected release")
    provisional = RollbackEvent(
        "rollback_pending", run_id, expected_release_id, release.parent_release_id, reason
    )
    identity = "rollback_" + hashlib.sha256(json_bytes(provisional)).hexdigest()
    event = replace(provisional, event_id=identity)
    current = active_release(root)
    event_path = root / "rollbacks" / f"{identity}.json"
    if current is None or current.release_id != expected_release_id:
        pointer = read_record(root / "active.json", ActiveRelease)
        if (
            event_path.exists()
            and pointer.release_id == release.parent_release_id
            and read_record(event_path, RollbackEvent) == event
        ):
            return event
        raise ValueError("Active release changed before rollback")
    write_bytes(event_path, json_bytes(event), immutable=True)
    write_bytes(root / "active.json", json_bytes(ActiveRelease(release.parent_release_id)))
    return event
