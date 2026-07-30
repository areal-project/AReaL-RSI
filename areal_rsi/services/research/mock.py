"""Mock ResearchAgent implementation."""

from __future__ import annotations

from typing import Any

from areal_rsi.schemas.core import Dataset, MemoryContext, ModelRef, TrainRun, Traj


class MockResearchAgent:
    """Deterministic ResearchAgent used by local tests and demos."""

    def generate_dataset(
        self,
        trajs: list[Traj],
        memory_context: MemoryContext,
        current_ckpt: str,
        *,
        envarena: object | None = None,
        model_ref: ModelRef | None = None,
        harness_config: dict[str, Any] | None = None,
        traj_filter: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
    ) -> Dataset:
        filtered = _filter_trajs(trajs, traj_filter or {})
        examples = [
            {
                "task": {
                    "task_id": traj["task_id"],
                    "harness_id": traj.get("harness_id"),
                },
                "experience": _experience_for_traj(traj),
                "source_traj_ids": [traj["traj_id"]],
                "metadata": {
                    "reward": traj.get("reward"),
                    "current_ckpt": current_ckpt,
                    "generation_method": "mock_rule_based",
                },
            }
            for traj in filtered
        ]
        return {
            "dataset_id": f"dataset_{current_ckpt}_{len(examples)}",
            "examples": examples,
            "metadata": {
                "source_num_trajs": len(trajs),
                "filtered_num_trajs": len(filtered),
                "memory_events": len(memory_context.get("events", [])),
                "config": config or {},
            },
        }

    def choose_ckpt(self, train_runs: list[TrainRun], memory_context: MemoryContext) -> str:
        if not train_runs:
            raise ValueError("No train runs available for checkpoint selection.")
        return str(train_runs[-1]["ckpt"])


def _filter_trajs(trajs: list[Traj], traj_filter: dict[str, Any]) -> list[Traj]:
    reward_lt = traj_filter.get("reward_lt")
    if reward_lt is None:
        return list(trajs)
    return [traj for traj in trajs if float(traj.get("reward", 0.0)) < float(reward_lt)]


def _experience_for_traj(traj: Traj) -> str:
    reward = float(traj.get("reward", 0.0))
    if reward >= 1.0:
        return "Preserve the successful solution pattern and avoid unnecessary changes."
    if reward <= 0.0:
        return "Use task evidence and failing feedback to localize the fix before patching."
    return "Refine near-miss solutions by checking tests and limiting patch scope."
