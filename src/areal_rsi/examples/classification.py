"""A second dataset/model recipe using the same RLVR training infrastructure."""

from __future__ import annotations

import sys

from areal_rsi.infra.coordination.evaluation.profile import PromptProfile
from areal_rsi.infra.coordination.evaluation.rlvr import prepare_training
from areal_rsi.infra.coordination.generation import cycle_main
from areal_rsi.scripts.train import TrainingPlan
from areal_rsi.scripts.train import main as train_main

PROFILE = PromptProfile(
    config_class="areal.api.cli_args.GRPOConfig",
    workflow="areal_rsi.infra.runtime.prompt.FrozenPromptWorkflow",
    reward_fn="areal_rsi.infra.coordination.evaluation.rlvr.exact_reward",
    verify_fn="areal_rsi.infra.coordination.evaluation.rlvr.exact_match",
    runner_directory="areal",
    boxed_answers=False,
    enabled_tools=(),
    researcher_instruction=(
        "Improve a reusable classification prompt with exactly one {prompt} field. "
        "Request a single label. Return only the template; omit task-specific questions, "
        "answers and IDs. Escape other braces by doubling them."
    ),
)


def cycle() -> None:
    cycle_main(PROFILE)


def prepare(argv: list[str]) -> TrainingPlan | None:
    return prepare_training(
        argv, reward_fn="areal_rsi.infra.coordination.evaluation.rlvr.exact_reward"
    )


if __name__ == "__main__":
    train_main(["--recipe", "areal_rsi.examples.classification.prepare", *sys.argv[1:]])
