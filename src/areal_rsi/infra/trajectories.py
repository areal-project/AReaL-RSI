"""Append-only local trajectory index; trace bytes stay in referenced artifacts."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from areal_rsi.api.control import encode_record
from areal_rsi.api.records import Trajectory, TrajectoryPage, TrajectoryQuery, TrajectoryRef
from areal_rsi.infra.artifacts import read_record


class LocalTrajectoryStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, trajectory_id: str) -> Path:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,150}", trajectory_id):
            raise ValueError("Invalid trajectory ID")
        return self.root / f"{trajectory_id}.json"

    async def save(self, trajectory: Trajectory) -> TrajectoryRef:
        path = self._path(trajectory.trajectory_id)
        # Publish complete bytes atomically without replacing an existing ID.
        fd, temporary = tempfile.mkstemp(prefix=".trajectory-", dir=self.root)
        try:
            with os.fdopen(fd, "w") as output:
                output.write(json.dumps(encode_record(trajectory), ensure_ascii=False) + "\n")
                output.flush()
                os.fsync(output.fileno())
            os.link(temporary, path)
        except FileExistsError:
            if await self.get(trajectory.trajectory_id) != trajectory:
                raise ValueError(
                    f"Trajectory ID already has different content: {path.stem}"
                ) from None
        finally:
            Path(temporary).unlink(missing_ok=True)
        return TrajectoryRef(trajectory.trajectory_id)

    async def get(self, trajectory_id: str) -> Trajectory | None:
        path = self._path(trajectory_id)
        return read_record(path, Trajectory) if path.exists() else None

    async def query(self, query: TrajectoryQuery) -> TrajectoryPage:
        records = [read_record(path, Trajectory) for path in sorted(self.root.glob("*.json"))]
        selected = [
            record
            for record in records
            if (query.task_id is None or record.task_id == query.task_id)
            and (query.solution_id is None or record.solution_id == query.solution_id)
            and (query.agent_id is None or record.agent_id == query.agent_id)
        ]
        start = int(query.cursor or "0")
        if start < 0:
            raise ValueError("Invalid trajectory cursor")
        page = selected[start : start + query.limit]
        end = start + len(page)
        return TrajectoryPage(
            tuple(TrajectoryRef(record.trajectory_id) for record in page),
            str(end) if end < len(selected) else None,
        )
