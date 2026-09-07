"""Minimal local smoke check for the TrajectoryStore framework contract."""

from __future__ import annotations

import asyncio
import json
import tempfile

from areal_rsi.api.base import (
    ContentRef,
    Trajectory,
    TrajectoryQuery,
    TrajectoryStorageType,
    TrajectoryStoreSpec,
)
from areal_rsi.framework import TrajectoryStoreLoader


async def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = TrajectoryStoreLoader().create(
            TrajectoryStoreSpec(
                store_id="smoke-local",
                provider="filesystem",
                storage_type=TrajectoryStorageType.LOCAL,
                uri=directory,
            )
        )
        ref = await store.save(
            Trajectory(
                trajectory_id="smoke-trajectory",
                agent_id="task-agent",
                content=ContentRef(
                    uri="content://smoke-trajectory",
                    digest="sha256:smoke-trajectory",
                ),
                usage={"tokens": 1},
                created_at="2026-09-05T00:00:00Z",
                task_id="smoke-task",
                solution_id="smoke-solution",
                source={"provider": "local"},
            )
        )
        page = await store.query(TrajectoryQuery(task_id="smoke-task"))
        bundle = await store.bundle(page.trajectories)
        print(
            json.dumps(
                {
                    "trajectory": ref.trajectory_id,
                    "store": ref.store_id,
                    "bundle": bundle.uri,
                    "digest": bundle.digest,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
