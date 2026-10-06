"""Train AReaL GRPO with a frozen Claude Code solving Agent."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from areal_rsi.api.records import RsiSplit
from areal_rsi.infra.coordination.evaluation.assets import _rows
from areal_rsi.infra.coordination.evaluation.tasks import load_task_set
from areal_rsi.infra.runtime.claude_solver import load_claude_harness
from areal_rsi.scripts.train import TrainingPlan, load_config


def prepare(argv: list[str]) -> TrainingPlan | None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-set-root", type=Path, required=True)
    parser.add_argument("--harness-root", type=Path, required=True)
    parser.add_argument("--claude-cli", type=Path, required=True)
    parser.add_argument("--train-cases", type=int)
    parser.add_argument("--dev-cases", type=int)
    parser.add_argument("--validate-only", action="store_true")
    args, areal_args = parser.parse_known_args(argv)

    config = load_config(areal_args)
    task_set = load_task_set(args.task_set_root)
    model_root = Path(config.actor.path)
    harness, _ = load_claude_harness(args.harness_root, args.claude_cli, model_root)
    if (
        config.scheduler.type != "slurm"
        or config.rollout.agent.mode != "inline"
        or config.rollout.agent.tool_call_parser != "qwen"
        or not config.rollout.dump_to_file
        or any(
            Path(path).resolve() != model_root.resolve()
            for path in (config.ref.path, config.tokenizer_path, config.sglang.model_path)
        )
        or harness.model != str(model_root.resolve())
    ):
        raise ValueError("Claude GRPO recipe differs from the frozen inputs")

    train_cases = (
        args.train_cases if args.train_cases is not None else len(task_set.model_train.task_ids)
    )
    dev_cases = args.dev_cases if args.dev_cases is not None else len(task_set.model_dev.task_ids)
    if (
        train_cases < 1
        or train_cases > len(task_set.model_train.task_ids)
        or dev_cases < 1
        or dev_cases > len(task_set.model_dev.task_ids)
        or config.train_dataset.batch_size > train_cases
        or config.valid_dataset.batch_size > dev_cases
    ):
        raise ValueError("Claude GRPO dataset sizes are invalid")

    answers: dict[str, str] = json.loads((args.task_set_root / "private/answers.json").read_bytes())
    train_rows = _rows(args.task_set_root, RsiSplit.MODEL_TRAIN, answers)[:train_cases]
    dev_rows = _rows(args.task_set_root, RsiSplit.MODEL_DEV, answers)[:dev_cases]
    if args.validate_only:
        print(
            json.dumps(
                {
                    "status": "validated",
                    "task_set_id": task_set.task_set_id,
                    "harness_id": harness.harness_id,
                    "train_cases": len(train_rows),
                    "dev_cases": len(dev_rows),
                }
            )
        )
        return None

    workflow_kwargs = {
        "task_set_root": str(args.task_set_root.resolve()),
        "harness_root": str(args.harness_root.resolve()),
        "claude_cli": str(args.claude_cli.resolve()),
        "backend_model_root": str(model_root.resolve()),
        "task_set_id": task_set.task_set_id,
        "harness_id": harness.harness_id,
    }
    return TrainingPlan(
        config,
        train_rows,
        dev_rows,
        "areal_rsi.infra.runtime.claude_solver.FrozenClaudeWorkflow",
        workflow_kwargs,
    )
