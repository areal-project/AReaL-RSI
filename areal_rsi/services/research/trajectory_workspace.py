"""File-backed trajectory inputs for one ResearchAgent iteration."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from areal_rsi.schemas.core import MemoryContext, Traj


@dataclass(frozen=True, slots=True)
class TrajectoryWorkspace:
    """Materialize large trajectory payloads without placing them in prompts."""

    root: Path

    @classmethod
    def create(cls, root: str | Path) -> "TrajectoryWorkspace":
        workspace = cls(Path(root).resolve())
        (workspace.root / "input" / "tasks").mkdir(parents=True, exist_ok=True)
        (workspace.root / "output" / "experiences").mkdir(parents=True, exist_ok=True)
        return workspace

    def materialize(
        self,
        *,
        trajs: list[Traj],
        memory_context: MemoryContext,
        run_context: dict[str, Any],
        base_train_config: dict[str, Any],
    ) -> dict[str, Any]:
        """Write one input file per task and return a compact file index."""

        grouped: defaultdict[str, list[Traj]] = defaultdict(list)
        for index, traj in enumerate(trajs):
            task_id = str(traj.get("task_id") or f"unknown_task_{index:06d}")
            grouped[task_id].append(traj)

        tasks: list[dict[str, Any]] = []
        for index, task_id in enumerate(sorted(grouped)):
            task_trajs = grouped[task_id]
            filename = f"{index:06d}_{_safe_name(task_id)}.json"
            task_path = self.root / "input" / "tasks" / filename
            _write_json(
                task_path,
                {
                    "schema_version": "research-task-input.v1",
                    "task_id": task_id,
                    "trajectories": task_trajs,
                },
            )
            rewards = [_as_float(traj.get("reward")) for traj in task_trajs]
            tasks.append(
                {
                    "task_id": task_id,
                    "path": str(task_path),
                    "num_trajectories": len(task_trajs),
                    "trajectory_ids": [
                        str(traj.get("traj_id", "")) for traj in task_trajs
                    ],
                    "rewards": rewards,
                    "statuses": [traj.get("status") for traj in task_trajs],
                }
            )

        memory_path = self.root / "input" / "memory.json"
        run_context_path = self.root / "input" / "run_context.json"
        train_config_path = self.root / "input" / "base_train_config.json"
        _write_json(memory_path, memory_context)
        _write_json(run_context_path, run_context)
        _write_json(train_config_path, base_train_config)

        manifest = {
            "schema_version": "trajectory-manifest.v1",
            "scope": {
                "run_id": run_context.get("run_id"),
                "iteration": run_context.get("iteration"),
            },
            "workspace": str(self.root),
            "task_count": len(tasks),
            "trajectory_count": len(trajs),
            "tasks": tasks,
            "memory_path": str(memory_path),
            "run_context_path": str(run_context_path),
            "base_train_config_path": str(train_config_path),
            "output": {
                "experiences_dir": str(self.root / "output" / "experiences"),
                # AReaL currently selects its SWE JSONL loader from a `swe`
                # token in the path, so keep that token in the generated name.
                "dataset_path": str(self.root / "output" / "swe_dataset.jsonl"),
                "train_config_path": str(self.root / "output" / "train_config.yaml"),
                "research_result_path": str(
                    self.root / "output" / "research_result.json"
                ),
                "checkpoint_decision_path": str(
                    self.root / "output" / "checkpoint_decision.json"
                ),
            },
        }
        manifest_path = self.root / "input" / "trajectory_manifest.json"
        _write_json(manifest_path, manifest)
        manifest["manifest_path"] = str(manifest_path)
        return manifest


def _safe_name(task_id: str) -> str:
    compact = re.sub(r"[^A-Za-z0-9._-]+", "_", task_id).strip("._-")
    compact = compact[:80] or "task"
    digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:10]
    return f"{compact}_{digest}"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=repr),
        encoding="utf-8",
    )


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
