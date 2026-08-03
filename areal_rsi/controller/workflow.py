"""Workflow steps executed by one AReaL-RSI iteration."""

from __future__ import annotations

from typing import Literal, TypeAlias

StepName: TypeAlias = Literal[
    "holdout_eval",
    "submit_eval",
    "collect_trajs",
    "generate_experiences",
    "skill_eval",
    "generate_dataset",
    "train",
    "record_decision_pending",
]

RSI_ITERATION_STEPS: tuple[StepName, ...] = (
    "holdout_eval",
    "submit_eval",
    "collect_trajs",
    "generate_experiences",
    "skill_eval",
    "generate_dataset",
    "train",
    "record_decision_pending",
)


def pending_steps(completed_steps: list[str]) -> list[str]:
    """Return workflow steps not yet marked complete."""

    completed = set(completed_steps)
    return [step for step in RSI_ITERATION_STEPS if step not in completed]
