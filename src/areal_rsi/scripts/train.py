"""Task-independent AReaL v2 training boundary and recipe entrypoint."""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast


@dataclass(frozen=True, slots=True)
class TrainingPlan:
    # AReaL config and dataset/workflow dictionaries are external plugin boundaries.
    config: Any
    train_rows: tuple[dict[str, Any], ...]
    valid_rows: tuple[dict[str, Any], ...] | None
    workflow: str
    workflow_kwargs: dict[str, Any]
    finalize: Callable[[], None] | None = None


def import_symbol(path: str) -> Any:
    module, name = path.rsplit(".", 1)
    return getattr(importlib.import_module(module), name)


def load_config(argv: list[str], config_class: str = "areal.api.cli_args.GRPOConfig") -> Any:
    from areal.api.cli_args import load_expr_config  # type: ignore[import-not-found]

    config, _ = load_expr_config(argv, import_symbol(config_class))
    validate_v2(config)
    return config


def validate_v2(config: Any) -> None:
    if config.scheduler.type != "slurm":
        raise ValueError("RSI training requires the Slurm scheduler")
    if config.actor._version != "v2" or config.rollout._version != "v2":
        raise ValueError("RSI requires actor._version=v2 and rollout._version=v2")


def areal_root() -> Path:
    import areal  # type: ignore[import-not-found]

    return Path(areal.__file__).resolve().parent.parent


def load_tokenizer(path: str) -> Any:
    from areal.utils.hf_utils import load_hf_tokenizer  # type: ignore[import-not-found]

    return load_hf_tokenizer(path)


def train(plan: TrainingPlan) -> None:
    """AReaL owns scheduling, worker creation, updates and trainer lifetime."""
    validate_v2(plan.config)
    if not plan.train_rows:
        raise ValueError("AReaL training needs a nonempty dataset")
    PPOTrainer = importlib.import_module("areal").PPOTrainer
    Dataset = importlib.import_module("datasets").Dataset

    dataset = Dataset.from_list(list(plan.train_rows))
    valid = Dataset.from_list(list(plan.valid_rows)) if plan.valid_rows is not None else None
    kwargs: dict[str, Any] = {
        "workflow": plan.workflow,
        "workflow_kwargs": plan.workflow_kwargs,
    }
    if valid is not None:
        kwargs.update(eval_workflow=plan.workflow, eval_workflow_kwargs=plan.workflow_kwargs)
    with PPOTrainer(plan.config, dataset, valid_dataset=valid) as trainer:
        trainer.train(**kwargs)
    if plan.finalize is not None:
        plan.finalize()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--recipe", required=True, help="Import path of a recipe preparation function"
    )
    args, recipe_args = parser.parse_known_args(argv)
    prepare = cast(
        Callable[[list[str]], TrainingPlan | None],
        import_symbol(args.recipe),
    )
    plan = prepare(recipe_args)
    if plan is not None:
        train(plan)


if __name__ == "__main__":
    main(sys.argv[1:])
