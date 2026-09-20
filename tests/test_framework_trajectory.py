"""Contract tests for the local TrajectoryStore implementation."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote, urlparse

from areal_rsi.api.interfaces import TrajectoryStore
from areal_rsi.api.records import (
    TRAJECTORY_BUNDLE_MEDIA_TYPE,
    ContentRef,
    Trajectory,
    TrajectoryQuery,
    TrajectoryStorageType,
    TrajectoryStoreSpec,
)
from areal_rsi.infra.storage.trajectory import TrajectoryStoreLoader


class FileTrajectoryStoreTest(unittest.IsolatedAsyncioTestCase):
    async def test_save_is_idempotent_but_never_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            trajectory = self._trajectory("trajectory_1")

            first = await store.save(trajectory)
            second = await store.save(trajectory)

            self.assertEqual(first, second)
            self.assertEqual((await store.get(first.trajectory_id)), trajectory)

            changed = self._trajectory("trajectory_1")
            changed.usage = {"tokens": 2}
            with self.assertRaises(FileExistsError):
                await store.save(changed)

    async def test_query_and_bundle_use_portable_references(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            first = await store.save(self._trajectory("trajectory_1", task_id="task_1"))
            await store.save(self._trajectory("trajectory_2", task_id="task_2"))

            page = await store.query(TrajectoryQuery(task_id="task_1", limit=1))
            bundle = await store.bundle(page.trajectories)

            self.assertEqual(page.trajectories, (first,))
            self.assertEqual(bundle.media_type, TRAJECTORY_BUNDLE_MEDIA_TYPE)
            payload = json.loads(Path(unquote(urlparse(bundle.uri).path)).read_text())
            self.assertEqual(payload["trajectories"][0]["trajectory_id"], "trajectory_1")
            self.assertEqual(payload["trajectories"][0]["store_id"], "local")

    @staticmethod
    def _store(directory: str) -> TrajectoryStore:
        return TrajectoryStoreLoader().create(
            TrajectoryStoreSpec(
                store_id="local",
                provider="filesystem",
                storage_type=TrajectoryStorageType.LOCAL,
                uri=directory,
            )
        )

    @staticmethod
    def _trajectory(
        trajectory_id: str,
        *,
        task_id: str = "task_1",
    ) -> Trajectory:
        return Trajectory(
            trajectory_id=trajectory_id,
            agent_id="task-agent",
            content=ContentRef(
                uri=f"content://{trajectory_id}",
                digest=f"sha256:{trajectory_id}",
            ),
            usage={"tokens": 1},
            created_at="2026-09-05T00:00:00Z",
            task_id=task_id,
            solution_id="solution_1",
            source={"provider": "local"},
        )


if __name__ == "__main__":
    unittest.main()
