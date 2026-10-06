"""AReaL 2.0 inference-only recipe for a frozen RSI prompt model/Harness pair."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from areal_rsi.api.control import json_bytes
from areal_rsi.api.records import RsiSplit
from areal_rsi.infra.coordination.evaluation.assets import (
    evaluation_rows,
    load_frozen_inputs,
    validate_areal_import_root,
    validate_frozen_model,
    validate_recipe_config,
    verifier_sources,
)
from areal_rsi.infra.coordination.evaluation.contracts import TraceBundle
from areal_rsi.infra.coordination.evaluation.evaluation import (
    complete_report,
    decode_trajectory,
    verified_case,
)
from areal_rsi.infra.coordination.evaluation.harness import load_prompt_harness
from areal_rsi.infra.coordination.evaluation.profile import load_profile, workflow_kwargs
from areal_rsi.infra.storage.artifacts import checkpoint_digest, source_files_digest
from areal_rsi.infra.storage.files import write_bytes
from areal_rsi.scripts.inference import rollout_batch
from areal_rsi.scripts.jobs import digest_bytes
from areal_rsi.scripts.train import areal_root, import_symbol, load_config, load_tokenizer


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--task-set-root", type=Path, required=True)
    parser.add_argument("--harness-root", type=Path, required=True)
    parser.add_argument("--base-model-digest", required=True)
    parser.add_argument("--split", type=RsiSplit, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validate-only", action="store_true")
    args, areal_args = parser.parse_known_args(argv)
    bundle_path = args.output.with_suffix(".trajectories.json")
    if not args.output.is_absolute():
        raise ValueError("prompt evaluation output path must be absolute")
    if args.output.exists() or bundle_path.exists():
        raise FileExistsError(args.output)
    if args.split is RsiSplit.MODEL_TRAIN:
        raise ValueError("Model training tasks cannot be evaluated as a selection split")

    profile = load_profile(args.profile)
    verify = import_symbol(profile.verify_fn)
    config = load_config(areal_args, profile.config_class)
    if config.gconfig.n_samples != 1:
        raise ValueError("RSI prompt evaluation requires exactly one sample per task")
    runner_root = areal_root() / profile.runner_directory
    validate_areal_import_root(runner_root, areal_root() / "areal/__init__.py")
    frozen = load_frozen_inputs(
        args.task_set_root, args.harness_root, runner_root, boxed_answers=profile.boxed_answers
    )
    if profile.enabled_tools != frozen.harness.enabled_tools:
        raise ValueError("Profile tools differ from the frozen Harness")
    validate_recipe_config(config, frozen, args.task_set_root)
    validate_frozen_model(config, args.base_model_digest, rl=True)
    if args.validate_only:
        print(
            json.dumps(
                {
                    "status": "config_validated",
                    "task_set_id": frozen.task_set.task_set_id,
                    "harness_id": frozen.harness.harness_id,
                    "model_digest": args.base_model_digest,
                    "split": args.split.value,
                }
            )
        )
        return
    rows = evaluation_rows(args.task_set_root, args.split, boxed_answers=profile.boxed_answers)
    tokenizer = load_tokenizer(config.tokenizer_path)

    verifier_files = verifier_sources(runner_root)
    verifier_digest = source_files_digest(verifier_files)
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
    cases = []
    traces = []
    results = rollout_batch(
        config,
        tuple(data for _, data in rows),
        profile.workflow,
        kwargs,
    )
    for (task_id, data), localized in zip(rows, results, strict=True):
        case = verified_case(
            task_id,
            localized,
            answer=data["answer"],
            decode=lambda ids: tokenizer.decode(ids, skip_special_tokens=False),
            verify=verify,
        )
        cases.append(case)
        traces.append(decode_trajectory(task_id, localized))
    if load_profile(args.profile) != profile:
        raise ValueError("Frozen evaluation profile changed")
    if checkpoint_digest(Path(config.actor.path)) != args.base_model_digest:
        raise ValueError("AReaL actor checkpoint changed during prompt evaluation")
    if load_prompt_harness(args.harness_root, runner_root)[0] != frozen.harness:
        raise ValueError("prompt Harness changed during AReaL evaluation")
    if verifier_digest != source_files_digest(verifier_files):
        raise ValueError("prompt verifier changed during AReaL evaluation")
    bundle = TraceBundle(
        1,
        frozen.task_set.task_set_id,
        args.split,
        args.base_model_digest,
        frozen.harness.harness_id,
        frozen.harness.runner_digest,
        verifier_digest,
        tuple(traces),
    )
    bundle_bytes = json_bytes(bundle)
    report = complete_report(
        frozen.task_set,
        args.split,
        cases,
        model_digest=args.base_model_digest,
        harness_id=frozen.harness.harness_id,
        runner_digest=frozen.harness.runner_digest,
        verifier_digest=verifier_digest,
        seed=config.seed,
        temperature=config.gconfig.temperature,
        max_new_tokens=config.gconfig.max_new_tokens,
        trajectory_path=str(bundle_path),
        trajectory_digest=digest_bytes(bundle_bytes),
    )
    write_bytes(bundle_path, bundle_bytes, immutable=True)
    write_bytes(args.output, json_bytes(report), immutable=True)


if __name__ == "__main__":
    main(sys.argv[1:])
