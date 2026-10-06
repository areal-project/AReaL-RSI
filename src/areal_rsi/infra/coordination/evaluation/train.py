"""AReaL 2.0 GRPO recipe using a frozen RSI prompt task set and Harness."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from areal_rsi.api.control import json_bytes, read_record, safe_name
from areal_rsi.api.research import RsiTrainIntent, RsiTrainResult
from areal_rsi.infra.coordination.evaluation.assets import (
    load_frozen_inputs,
    validate_areal_import_root,
    validate_frozen_model,
    validate_grpo_signal_config,
    validate_recipe_config,
    validate_train_identity,
    verifier_sources,
)
from areal_rsi.infra.coordination.evaluation.harness import load_prompt_harness
from areal_rsi.infra.coordination.evaluation.profile import load_profile, workflow_kwargs
from areal_rsi.infra.storage.artifacts import (
    checkpoint_digest,
    latest_hf_checkpoint,
    rollout_dump_evidence,
    rollout_dump_roots,
    source_files_digest,
)
from areal_rsi.infra.storage.files import write_bytes
from areal_rsi.scripts.train import (
    TrainingPlan,
    areal_root,
    import_symbol,
    load_config,
    load_tokenizer,
)


def prepare(argv: list[str]) -> TrainingPlan | None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--task-set-root", type=Path, required=True)
    parser.add_argument("--harness-root", type=Path, required=True)
    parser.add_argument("--base-model-digest", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--reconcile-only", action="store_true")
    args, areal_args = parser.parse_known_args(argv)
    safe_name(args.run_id)

    profile = load_profile(args.profile)
    verify = import_symbol(profile.verify_fn)
    config = load_config(areal_args, profile.config_class)
    runner_root = areal_root() / profile.runner_directory
    validate_areal_import_root(runner_root, areal_root() / "areal/__init__.py")
    frozen = load_frozen_inputs(
        args.task_set_root, args.harness_root, runner_root, boxed_answers=profile.boxed_answers
    )
    if profile.enabled_tools != frozen.harness.enabled_tools:
        raise ValueError("Profile tools differ from the frozen Harness")
    validate_recipe_config(config, frozen, args.task_set_root)
    validate_grpo_signal_config(config)
    validate_frozen_model(config, args.base_model_digest, rl=True)
    verifier_files = verifier_sources(runner_root)
    verifier_digest = source_files_digest(verifier_files)
    config_digest = (
        "sha256:" + hashlib.sha256(json_bytes(asdict(config)) + json_bytes(profile)).hexdigest()
    )
    validate_train_identity(config, args.run_id)
    for row in (*frozen.train_rows, *frozen.dev_rows):
        answer = row["answer"]
        if verify(answer, answer) != 1.0:
            raise ValueError("Frozen prompt training or dev answer is not self-verifiable")
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
        return None
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

        tokenizer = load_tokenizer(config.tokenizer_path)
        if not tokenizer.encode(frozen.prompt_template.format(prompt="prompt preflight")):
            raise ValueError("Frozen prompt prompt produced no tokenizer input")
        kwargs = workflow_kwargs(
            profile,
            config,
            prompt_template=frozen.prompt_template,
            task_set_root=args.task_set_root,
            harness_root=args.harness_root,
            task_set_id=frozen.task_set.task_set_id,
            harness_id=frozen.harness.harness_id,
            runner_digest=frozen.harness.runner_digest,
            verifier_digest=verifier_digest,
            model_digest=args.base_model_digest,
        )

    def finalize() -> None:
        if load_profile(args.profile) != profile:
            raise ValueError("Frozen training profile changed")
        validate_frozen_model(config, args.base_model_digest, rl=True)
        if load_prompt_harness(args.harness_root, runner_root)[0] != frozen.harness:
            raise ValueError("prompt Harness changed during AReaL training")
        if source_files_digest(verifier_files) != verifier_digest:
            raise ValueError("prompt reward source changed during AReaL training")
        current_inputs = load_frozen_inputs(
            args.task_set_root, args.harness_root, runner_root, boxed_answers=profile.boxed_answers
        )
        if current_inputs.task_set != frozen.task_set:
            raise ValueError("prompt task set changed during AReaL training")
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

    if args.reconcile_only:
        finalize()
        return None
    return TrainingPlan(
        config,
        frozen.train_rows,
        frozen.dev_rows,
        profile.workflow,
        kwargs,
        finalize,
    )
