"""Mock ResearchAgent implementation."""

from __future__ import annotations

from collections import defaultdict
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
        stage = str((config or {}).get("stage", "experience_generation"))
        if stage == "distilled_dataset":
            examples = [
                {
                    "task": {
                        "task_id": traj["task_id"],
                        "harness_id": traj.get("harness_id"),
                    },
                    "messages": _training_messages(traj),
                    "source_traj_ids": [traj["traj_id"]],
                    "metadata": {
                        "reward": traj.get("reward"),
                        "current_ckpt": current_ckpt,
                        "generation_method": "mock_skill_distillation",
                        "experience_removed_from_prompt": True,
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
                    "stage": stage,
                },
            }

        by_task: defaultdict[str, list[Traj]] = defaultdict(list)
        for traj in filtered:
            by_task[str(traj["task_id"])].append(traj)
        examples = []
        for task_id in sorted(by_task):
            task_trajs = by_task[task_id]
            examples.append(
                {
                    "task": {
                        "task_id": task_id,
                        "harness_id": task_trajs[0].get("harness_id"),
                    },
                    "experience": _experience_for_task(task_trajs),
                    "source_traj_ids": [traj["traj_id"] for traj in task_trajs],
                    "metadata": {
                        "rewards": [traj.get("reward") for traj in task_trajs],
                        "current_ckpt": current_ckpt,
                        "generation_method": "mock_rule_based",
                    },
                }
            )
        return {
            "dataset_id": f"dataset_{current_ckpt}_{len(examples)}",
            "examples": examples,
            "metadata": {
                "source_num_trajs": len(trajs),
                "filtered_num_trajs": len(filtered),
                "memory_events": len(memory_context.get("events", [])),
                "stage": stage,
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


def _experience_for_task(trajs: list[Traj]) -> str:
    rewards = [float(traj.get("reward", 0.0)) for traj in trajs]
    if any(reward >= 1.0 for reward in rewards):
        return "Preserve the successful solution pattern and avoid unnecessary changes."
    if all(reward <= 0.0 for reward in rewards):
        return "Use task evidence and failing feedback to localize the fix before patching."
    return "Refine near-miss solutions by checking tests and limiting patch scope."


def _training_messages(traj: Traj) -> list[dict[str, Any]]:
    """Use the original prompt with the successful skill-assisted completion."""

    messages = traj.get("messages", [])
    original = traj.get("metadata", {}).get("original_messages")
    prompt_messages = original if isinstance(original, list) else messages
    restored = [dict(message) for message in prompt_messages if isinstance(message, dict)]
    distilled = [dict(message) for message in messages if isinstance(message, dict)]
    distilled_outputs = [
        message for message in distilled if message.get("role") in {"assistant", "tool"}
    ]
    prompt = [
        message for message in restored if message.get("role") in {"system", "user"}
    ]
    return prompt + distilled_outputs
