"""Prepare local prompt/answer datasets for any single-turn reward task."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from areal_rsi.api.control import decode
from areal_rsi.scripts.train import TrainingPlan, load_config


@dataclass(frozen=True, slots=True)
class PromptAnswerTask:
    task_id: str
    prompt: str
    answer: str

    def __post_init__(self) -> None:
        if not self.task_id.strip() or not self.prompt.strip() or not self.answer.strip():
            raise ValueError("Prompt/answer tasks need nonempty identity, prompt and answer")


def dataset_rows(path: Path) -> tuple[dict[str, Any], ...]:
    tasks = tuple(
        decode(PromptAnswerTask, json.loads(line))
        for line in path.read_bytes().splitlines()
        if line.strip()
    )
    if not tasks or len({task.task_id for task in tasks}) != len(tasks):
        raise ValueError("Dataset must contain nonempty, unique task identities")
    return tuple(
        {
            "task_id": task.task_id,
            "messages": [{"role": "user", "content": task.prompt}],
            "answer": task.answer,
        }
        for task in tasks
    )


def prepare_training(argv: list[str], *, reward_fn: str) -> TrainingPlan | None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--dev-data", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    args, areal_args = parser.parse_known_args(argv)
    config = load_config(areal_args)
    rows = dataset_rows(args.data)
    valid = dataset_rows(args.dev_data) if args.dev_data else None
    if len(rows) < config.train_dataset.batch_size:
        raise ValueError("Training needs a complete local batch")
    if valid is not None:
        if len(valid) < config.valid_dataset.batch_size:
            raise ValueError("Validation needs a complete local batch")
        if {row["task_id"] for row in rows} & {row["task_id"] for row in valid}:
            raise ValueError("Training and validation task identities overlap")
    if any(
        path != config.actor.path
        for path in (config.ref.path, config.tokenizer_path, config.sglang.model_path)
    ):
        raise ValueError("Actor, reference, tokenizer and inference must use the same model")
    if args.validate_only:
        print(f"Validated {len(rows)} tasks for {config.actor.path}")
        return None
    return TrainingPlan(
        config,
        rows,
        valid,
        "areal.workflow.rlvr.RLVRWorkflow",
        {
            "reward_fn": reward_fn,
            "gconfig": config.gconfig,
            "tokenizer": config.tokenizer_path,
            "enable_thinking": False,
        },
    )


def exact_reward(
    prompt: str,
    completions: str,
    prompt_ids: list[int],
    completion_ids: list[int],
    answer: str,
    **metadata: Any,
) -> float:
    return float(completions.strip().removesuffix("<|im_end|>").strip() == answer)


def exact_match(response: str, answer: str) -> float:
    return float(response.strip().removesuffix("<|im_end|>").strip() == answer)
