"""Arithmetic reward and recipe selection for the shared local RLVR trainer."""

from __future__ import annotations

import sys

from areal_rsi.infra.coordination.evaluation.rlvr import prepare_training
from areal_rsi.scripts.train import TrainingPlan
from areal_rsi.scripts.train import main as train_main


def prepare(argv: list[str]) -> TrainingPlan | None:
    return prepare_training(
        argv, reward_fn="areal_rsi.infra.coordination.evaluation.tir.verifier.arithmetic_reward"
    )


def main() -> None:
    train_main(["--recipe", "areal_rsi.examples.arithmetic.prepare", *sys.argv[1:]])


if __name__ == "__main__":
    main()
