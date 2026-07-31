"""Workflow definitions for the first AReaL-RSI iteration loop."""

from __future__ import annotations

from typing import Literal, TypeAlias

StepName: TypeAlias = Literal[
    "submit_eval",
    "collect_trajs",
    "generate_dataset",
    "train",
    "holdout_eval",
    "record_decision_pending",
]

RSI_ITERATION_STEPS: tuple[StepName, ...] = (
    "submit_eval",
    "collect_trajs",
    "generate_dataset",
    "train",
    "holdout_eval",
    "record_decision_pending",
)


def pending_steps(completed_steps: list[str]) -> list[str]:
    """Return workflow steps not yet marked complete."""

    completed = set(completed_steps)
    return [step for step in RSI_ITERATION_STEPS if step not in completed]
