"""AReaL 2.0 GRPO recipe using a frozen RSI TIR task set and Harness."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

from areal_rsi.api.control import json_bytes, read_record, safe_name
from areal_rsi.api.research import RsiTrainIntent, RsiTrainResult
from areal_rsi.infra.storage.artifacts import (
    checkpoint_digest,
    latest_hf_checkpoint,
    rollout_dump_evidence,
    rollout_dump_roots,
    source_files_digest,
)
from areal_rsi.infra.storage.files import write_bytes
from areal_rsi_tir.assets import (
    RSI_REWARD_MAX_RETRIES,
    RSI_REWARD_TIMEOUT_SECONDS,
    load_frozen_tir_inputs,
    tir_verifier_files,
    validate_areal_import_root,
    validate_frozen_model,
    validate_tir_grpo_signal_config,
    validate_tir_recipe_config,
    validate_tir_train_identity,
)
from areal_rsi_tir.harness import load_tir_harness
from areal_rsi_tir.verifier import strict_math_reward


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--task-set-root", type=Path, required=True)
    parser.add_argument("--harness-root", type=Path, required=True)
    parser.add_argument("--base-model-digest", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--reconcile-only", action="store_true")
    args, areal_args = parser.parse_known_args(argv)
    safe_name(args.run_id)

    # AReaL and datasets are installed in the GPU recipe environment, not RSI's core env.
    import areal  # type: ignore[import-not-found]
    from areal import PPOTrainer
    from areal.api.cli_args import load_expr_config  # type: ignore[import-not-found]
    from areal.utils.hf_utils import load_hf_tokenizer  # type: ignore[import-not-found]
    from datasets import Dataset  # type: ignore
    from examples.tir import tir_workflow  # type: ignore[import-not-found]
    from examples.tir.tir_workflow import TIRGRPOConfig  # type: ignore[import-not-found]

    config, _ = load_expr_config(areal_args, TIRGRPOConfig)
    runner_root = Path(tir_workflow.__file__).resolve().parent
    validate_areal_import_root(runner_root, Path(areal.__file__))
    frozen = load_frozen_tir_inputs(args.task_set_root, args.harness_root, runner_root)
    validate_tir_recipe_config(config, frozen, args.task_set_root)
    validate_tir_grpo_signal_config(config)
    validate_frozen_model(config, args.base_model_digest, rl=True)
    verifier_files = tir_verifier_files(runner_root)
    verifier_digest = source_files_digest(verifier_files)
    config_digest = "sha256:" + hashlib.sha256(json_bytes(asdict(config))).hexdigest()
    validate_tir_train_identity(config, args.run_id)
    for row in (*frozen.train_rows, *frozen.dev_rows):
        answer = row["answer"]
        if strict_math_reward(answer, answer) != 1.0:
            raise ValueError("Frozen TIR training or dev answer is not self-verifiable")
    if args.validate_only:
        print(
            json.dumps(
                {
                    "status": "config_validated",
                    "task_set_id": frozen.task_set.task_set_id,
                    "harness_id": frozen.harness.harness_id,
                    "model_digest": args.base_model_digest,
                    "run_id": args.run_id,
                }
            )
        )
        return
    if args.receipt.exists():
        raise FileExistsError(args.receipt)
    intent_path = args.receipt.with_suffix(".intent.json")
    intent = RsiTrainIntent(
        args.run_id,
        frozen.task_set.task_set_id,
        frozen.harness.harness_id,
        args.base_model_digest,
        frozen.harness.runner_digest,
        verifier_digest,
        config_digest,
    )
    save_root = (
        Path(config.saver.fileroot)
        / "checkpoints"
        / getpass.getuser()
        / config.saver.experiment_name
        / config.saver.trial_name
        / "default"
    )
    prior_dumps = rollout_dump_roots(
        Path(config.rollout.fileroot),
        config.rollout.experiment_name,
        config.rollout.trial_name,
    )
    if args.reconcile_only:
        if read_record(intent_path, RsiTrainIntent) != intent:
            raise ValueError("AReaL trial inputs differ from the original training intent")
        if not save_root.is_dir() or not prior_dumps:
            raise FileNotFoundError("AReaL trial has no checkpoint and rollout to reconcile")
    else:
        if intent_path.exists():
            raise FileExistsError("AReaL training intent already exists; reconcile before relaunch")
        if save_root.exists() and any(save_root.iterdir()):
            raise FileExistsError("AReaL trial already has checkpoints; reconcile before relaunch")
        if prior_dumps:
            raise FileExistsError(
                "AReaL trial already has rollout dumps; reconcile before relaunch"
            )
        write_bytes(intent_path, json_bytes(intent), immutable=True)

        tokenizer = load_hf_tokenizer(config.tokenizer_path)
        if not tokenizer.encode(frozen.prompt_template.format(prompt="TIR preflight")):
            raise ValueError("Frozen TIR prompt produced no tokenizer input")
        train_dataset = Dataset.from_list(list(frozen.train_rows))
        valid_dataset = Dataset.from_list(list(frozen.dev_rows))
        workflow_kwargs = {
            "reward_fn": "areal_rsi_tir.verifier.strict_tir_reward_fn",
            "gconfig": config.gconfig,
            "tokenizer": config.tokenizer_path,
            "tir_config": config.tir,
            "enable_thinking": False,
            "prompt_template": frozen.prompt_template,
            "task_set_root": str(args.task_set_root.resolve()),
            "harness_root": str(args.harness_root.resolve()),
            "model_root": str(Path(config.actor.path).resolve()),
            "task_set_id": frozen.task_set.task_set_id,
            "harness_id": frozen.harness.harness_id,
            "runner_digest": frozen.harness.runner_digest,
            "verifier_digest": verifier_digest,
            "model_digest": args.base_model_digest,
            "fail_on_reward_timeout": True,
            "reward_timeout_seconds": RSI_REWARD_TIMEOUT_SECONDS,
            "reward_max_retries": RSI_REWARD_MAX_RETRIES,
        }
        with PPOTrainer(config, train_dataset, valid_dataset) as trainer:
            trainer.train(
                workflow="areal_rsi_tir.workflow.FrozenTIRWorkflow",
                workflow_kwargs=workflow_kwargs,
                eval_workflow="areal_rsi_tir.workflow.FrozenTIRWorkflow",
                eval_workflow_kwargs=workflow_kwargs,
            )
    validate_frozen_model(config, args.base_model_digest, rl=True)
    if load_tir_harness(args.harness_root, runner_root)[0] != frozen.harness:
        raise ValueError("TIR Harness changed during AReaL training")
    if source_files_digest(verifier_files) != verifier_digest:
        raise ValueError("TIR reward source changed during AReaL training")
    current_inputs = load_frozen_tir_inputs(args.task_set_root, args.harness_root, runner_root)
    if current_inputs.task_set != frozen.task_set:
        raise ValueError("TIR task set changed during AReaL training")
    checkpoint, step = latest_hf_checkpoint(save_root, require_unique=True)
    dump_roots = rollout_dump_roots(
        Path(config.rollout.fileroot),
        config.rollout.experiment_name,
        config.rollout.trial_name,
    )
    if len(dump_roots) != 1:
        raise ValueError("AReaL training must produce one unambiguous rollout dump root")
    rollout = rollout_dump_evidence(
        dump_roots[0],
        allowed_task_ids=frozenset(frozen.task_set.model_train.task_ids),
    )
    if rollout.tasks != len(frozen.task_set.model_train.task_ids):
        raise ValueError("AReaL rollout dump does not cover every model_train task")
    result = RsiTrainResult(
        args.run_id,
        "grpo",
        frozen.task_set.task_set_id,
        frozen.harness.harness_id,
        args.base_model_digest,
        checkpoint_digest(checkpoint),
        frozen.harness.runner_digest,
        verifier_digest,
        config_digest,
        str(checkpoint.resolve()),
        step,
        rollout,
    )
    write_bytes(args.receipt, json_bytes(result), immutable=True)


if __name__ == "__main__":
    main(sys.argv[1:])
