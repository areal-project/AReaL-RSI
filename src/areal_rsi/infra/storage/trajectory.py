"""Portable file-URI trajectory records and configurable store providers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

from areal_rsi.api.interfaces import TrajectoryStore, TrajectoryStoreFactory
from areal_rsi.api.records import (
    TRAJECTORY_BUNDLE_MEDIA_TYPE,
    ContentRef,
    Trajectory,
    TrajectoryPage,
    TrajectoryQuery,
    TrajectoryRef,
    TrajectoryStorageType,
    TrajectoryStoreSpec,
)
from areal_rsi.infra.storage.files import write_bytes


class PortableTrajectoryStore:
    """Append-only TrajectoryStore backed by a local filesystem directory."""

    def __init__(self, spec: TrajectoryStoreSpec):
        if spec.storage_type is not TrajectoryStorageType.LOCAL:
            raise ValueError("PortableTrajectoryStore requires storage_type='local'.")
        self.spec = spec
        self.root = self._path_from_uri(spec.uri)
        self._trajectory_root = self.root / "trajectories"
        self._bundle_root = self.root / "bundles"
        self._trajectory_root.mkdir(parents=True, exist_ok=True)
        self._bundle_root.mkdir(parents=True, exist_ok=True)

    async def save(self, trajectory: Trajectory) -> TrajectoryRef:
        payload = self._json_bytes(asdict(trajectory))
        path = self._trajectory_path(trajectory.trajectory_id)
        self._write_once(path, payload)
        return TrajectoryRef(
            trajectory_id=trajectory.trajectory_id,
            store_id=self.spec.store_id,
        )

    async def get(self, trajectory_id: str) -> Trajectory | None:
        path = self._trajectory_path(trajectory_id)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        content = payload.pop("content")
        return Trajectory(content=ContentRef(**content), **payload)

    async def query(self, query: TrajectoryQuery) -> TrajectoryPage:
        offset = self._parse_cursor(query.cursor)
        matches: list[Trajectory] = []
        for path in sorted(self._trajectory_root.glob("*.json")):
            trajectory = await self.get(self._id_from_path(path))
            if trajectory is not None and self._matches(trajectory, query):
                matches.append(trajectory)

        selected = matches[offset : offset + query.limit]
        next_offset = offset + len(selected)
        next_cursor = str(next_offset) if next_offset < len(matches) else None
        return TrajectoryPage(
            trajectories=tuple(
                TrajectoryRef(item.trajectory_id, store_id=self.spec.store_id) for item in selected
            ),
            next_cursor=next_cursor,
        )

    async def bundle(self, trajectories: Sequence[TrajectoryRef]) -> ContentRef:
        records: list[dict[str, Any]] = []
        for ref in trajectories:
            if ref.store_id not in (None, self.spec.store_id):
                raise ValueError(
                    f"Trajectory {ref.trajectory_id!r} belongs to store "
                    f"{ref.store_id!r}, not {self.spec.store_id!r}."
                )
            trajectory = await self.get(ref.trajectory_id)
            if trajectory is None:
                raise KeyError(f"Unknown trajectory {ref.trajectory_id!r}.")
            records.append(
                {
                    "trajectory_id": trajectory.trajectory_id,
                    "store_id": self.spec.store_id,
                    "content": asdict(trajectory.content),
                }
            )

        payload = self._json_bytes({"trajectories": records})
        digest = self._digest(payload)
        path = self._bundle_root / f"{digest.removeprefix('sha256:')}.json"
        self._write_once(path, payload)
        return ContentRef(
            uri=path.resolve().as_uri(),
            digest=digest,
            media_type=TRAJECTORY_BUNDLE_MEDIA_TYPE,
        )

    def _trajectory_path(self, trajectory_id: str) -> Path:
        if not trajectory_id:
            raise ValueError("trajectory_id must not be empty.")
        return self._trajectory_root / f"{quote(trajectory_id, safe='')}.json"

    @staticmethod
    def _id_from_path(path: Path) -> str:
        return unquote(path.stem)

    @staticmethod
    def _matches(trajectory: Trajectory, query: TrajectoryQuery) -> bool:
        return (
            (query.task_id is None or trajectory.task_id == query.task_id)
            and (query.solution_id is None or trajectory.solution_id == query.solution_id)
            and (query.agent_id is None or trajectory.agent_id == query.agent_id)
        )

    @staticmethod
    def _parse_cursor(cursor: str | None) -> int:
        if cursor is None:
            return 0
        try:
            offset = int(cursor)
        except ValueError as exc:
            raise ValueError("TrajectoryQuery.cursor must be a non-negative integer.") from exc
        if offset < 0:
            raise ValueError("TrajectoryQuery.cursor must be a non-negative integer.")
        return offset

    @staticmethod
    def _path_from_uri(uri: str) -> Path:
        parsed = urlparse(uri)
        if parsed.scheme == "file":
            if parsed.netloc not in ("", "localhost"):
                raise ValueError("PortableTrajectoryStore does not support remote file hosts.")
            return Path(unquote(parsed.path)).expanduser()
        if parsed.scheme:
            raise ValueError("PortableTrajectoryStore uri must be a local path or file URI.")
        return Path(uri).expanduser()

    @staticmethod
    def _write_once(path: Path, payload: bytes) -> None:
        try:
            write_bytes(path, payload, immutable=True)
        except ValueError as exc:
            raise FileExistsError(
                f"Refusing to overwrite immutable trajectory data at {path}."
            ) from exc

    @staticmethod
    def _json_bytes(payload: object) -> bytes:
        return (
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode()

    @staticmethod
    def _digest(payload: bytes) -> str:
        return f"sha256:{hashlib.sha256(payload).hexdigest()}"


class PortableTrajectoryStoreFactory:
    def create(self, spec: TrajectoryStoreSpec) -> TrajectoryStore:
        if spec.provider != "filesystem":
            raise ValueError(
                f"PortableTrajectoryStoreFactory cannot load provider {spec.provider!r}."
            )
        return PortableTrajectoryStore(spec)


class TrajectoryStoreLoader:
    """Resolve local or remote TrajectoryStores by provider name."""

    def __init__(
        self,
        factories: Mapping[str, TrajectoryStoreFactory] | None = None,
    ):
        self._factories: dict[str, TrajectoryStoreFactory] = {
            "filesystem": PortableTrajectoryStoreFactory(),
            **dict(factories or {}),
        }

    def register(self, provider: str, factory: TrajectoryStoreFactory) -> None:
        if not provider.strip():
            raise ValueError("TrajectoryStore provider must not be empty.")
        self._factories[provider] = factory

    def create(self, spec: TrajectoryStoreSpec) -> TrajectoryStore:
        try:
            factory = self._factories[spec.provider]
        except KeyError as exc:
            raise ValueError(
                f"No TrajectoryStoreFactory registered for provider {spec.provider!r}."
            ) from exc
        return factory.create(spec)
