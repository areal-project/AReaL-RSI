"""AReaL 2.0 inference-only recipe for a frozen RSI TIR model/Harness pair."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from areal_rsi.api.control import json_bytes
from areal_rsi.api.records import RsiSplit
from areal_rsi.infra.storage.files import write_bytes
from areal_rsi.scripts.jobs import digest_bytes
from areal_rsi_tir.assets import (
    RSI_REWARD_MAX_RETRIES,
    RSI_REWARD_TIMEOUT_SECONDS,
    checkpoint_digest,
    evaluation_rows,
    load_frozen_tir_inputs,
    source_files_digest,
    tir_verifier_files,
    validate_areal_import_root,
    validate_frozen_model,
    validate_tir_recipe_config,
)
from areal_rsi_tir.contracts import TirTraceBundle
from areal_rsi_tir.evaluation import (
    complete_tir_report,
    tir_trajectory,
    verified_tir_case,
)
from areal_rsi_tir.harness import load_tir_harness
from areal_rsi_tir.verifier import strict_math_reward


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--task-set-root", type=Path, required=True)
    parser.add_argument("--harness-root", type=Path, required=True)
    parser.add_argument("--base-model-digest", required=True)
    parser.add_argument("--split", type=RsiSplit, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validate-only", action="store_true")
    args, areal_args = parser.parse_known_args(argv)
    bundle_path = args.output.with_suffix(".trajectories.json")
    if not args.output.is_absolute():
        raise ValueError("TIR evaluation output path must be absolute")
    if args.output.exists() or bundle_path.exists():
        raise FileExistsError(args.output)
    if args.split is RsiSplit.MODEL_TRAIN:
        raise ValueError("Model training tasks cannot be evaluated as a selection split")

    # These imports are available in AReaL's recipe environment, not RSI's core env.
    import areal  # type: ignore[import-not-found]
    from areal.api.alloc_mode import ModelAllocation  # type: ignore[import-not-found]
    from areal.api.cli_args import (  # type: ignore[import-not-found]
        SGLangConfig,
        load_expr_config,
        vLLMConfig,
    )
    from areal.engine import (  # type: ignore[import-not-found]
        RemoteSGLangEngine,
        RemotevLLMEngine,
    )
    from areal.infra import LocalScheduler, SlurmScheduler  # type: ignore[import-not-found]
    from areal.infra.rpc.rtensor import RTensor  # type: ignore[import-not-found]
    from areal.utils.hf_utils import load_hf_tokenizer  # type: ignore[import-not-found]
    from examples.tir import tir_workflow  # type: ignore[import-not-found]
    from examples.tir.tir_workflow import TIRGRPOConfig  # type: ignore[import-not-found]

    config, _ = load_expr_config(areal_args, TIRGRPOConfig)
    if config.gconfig.n_samples != 1:
        raise ValueError("RSI TIR evaluation requires exactly one sample per task")
    runner_root = Path(tir_workflow.__file__).resolve().parent
    validate_areal_import_root(runner_root, Path(areal.__file__))
    frozen = load_frozen_tir_inputs(args.task_set_root, args.harness_root, runner_root)
    validate_tir_recipe_config(config, frozen, args.task_set_root)
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
    rows = evaluation_rows(args.task_set_root, args.split)
    tokenizer = load_hf_tokenizer(config.tokenizer_path)

    scheduler = (
        LocalScheduler(exp_config=config)
        if config.scheduler.type == "local"
        else SlurmScheduler(exp_config=config)
    )
    allocation = ModelAllocation.from_str(config.rollout.backend, name="rollout")
    if allocation.backend == "sglang":
        engine_type = RemoteSGLangEngine
        server_args = SGLangConfig.build_args(
            sglang_config=config.sglang,
            tp_size=allocation.parallel.tp_size,
            base_gpu_id=0,
        )
    elif allocation.backend == "vllm":
        engine_type = RemotevLLMEngine
        server_args = vLLMConfig.build_args(
            vllm_config=config.vllm,
            tp_size=allocation.parallel.tp_size,
            pp_size=allocation.parallel.pp_size,
        )
    else:
        raise ValueError("Unsupported AReaL evaluator rollout backend")
    config.rollout.max_head_offpolicyness = 0
    # This immutable evaluation stays at model version zero. The training
    # batch size would otherwise cap accepted rollouts for the entire run.
    config.rollout.consumer_batch_size = len(rows)
    config.rollout.deterministic_sampling = True
    engine = engine_type.as_controller(config.rollout, scheduler)
    verifier_files = tir_verifier_files(runner_root)
    verifier_digest = source_files_digest(verifier_files)
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
    cases = []
    traces = []
    try:
        engine.initialize(role="eval-rollout", server_args=server_args)
        for index, (_task_id, data) in enumerate(rows):
            engine.submit(
                data,
                workflow="areal_rsi_tir.workflow.FrozenTIRWorkflow",
                workflow_kwargs=workflow_kwargs,
                task_id=index,
                group_size=1,
                is_eval=True,
            )
        results = engine.wait(len(rows), timeout=None)
        if len(results) != len(rows):
            raise RuntimeError("AReaL returned an incomplete TIR evaluation batch")
        for (task_id, data), result in zip(rows, results, strict=True):
            if result is None:
                raise RuntimeError(f"AReaL returned no trajectory for task {task_id}")
            localized = RTensor.localize(result)
            case = verified_tir_case(
                task_id,
                localized,
                answer=data["answer"],
                decode=lambda ids: tokenizer.decode(ids, skip_special_tokens=False),
                verify=strict_math_reward,
            )
            cases.append(case)
            traces.append(tir_trajectory(task_id, localized))
    finally:
        engine.destroy()
    if checkpoint_digest(Path(config.actor.path)) != args.base_model_digest:
        raise ValueError("AReaL actor checkpoint changed during TIR evaluation")
    if load_tir_harness(args.harness_root, runner_root)[0] != frozen.harness:
        raise ValueError("TIR Harness changed during AReaL evaluation")
    if verifier_digest != source_files_digest(verifier_files):
        raise ValueError("TIR verifier changed during AReaL evaluation")
    bundle = TirTraceBundle(
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
    report = complete_tir_report(
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
