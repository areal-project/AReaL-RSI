"""Workflow definitions for the first AReaL-RSI iteration loop."""

from __future__ import annotations

from typing import Literal, TypeAlias

StepName: TypeAlias = Literal[
    "ensure_rollout_endpoint",
    "register_model",
    "submit_eval",
    "collect_trajs",
    "generate_dataset",
    "train",
    "update_endpoint",
    "holdout_eval",
    "record_decision_pending",
]

RSI_ITERATION_STEPS: tuple[StepName, ...] = (
    "ensure_rollout_endpoint",
    "register_model",
    "submit_eval",
    "collect_trajs",
    "generate_dataset",
    "train",
    "update_endpoint",
    "holdout_eval",
    "record_decision_pending",
)


def pending_steps(completed_steps: list[str]) -> list[str]:
    """Return workflow steps not yet marked complete."""

    completed = set(completed_steps)
    return [step for step in RSI_ITERATION_STEPS if step not in completed]
