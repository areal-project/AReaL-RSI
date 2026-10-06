"""Task-independent inference batches through AReaL's Slurm controllers."""

from __future__ import annotations

from typing import Any

from areal_rsi.scripts.train import validate_v2


def rollout_batch(
    config: Any,
    rows: tuple[dict[str, Any], ...],
    workflow: str,
    workflow_kwargs: dict[str, Any],
) -> tuple[dict[str, Any], ...]:
    validate_v2(config)
    if not rows:
        raise ValueError("Inference needs a nonempty batch")
    from areal.api.alloc_mode import ModelAllocation  # type: ignore[import-not-found]
    from areal.api.cli_args import SGLangConfig, vLLMConfig  # type: ignore[import-not-found]
    from areal.engine import (  # type: ignore[import-not-found]
        RemoteSGLangEngine,
        RemotevLLMEngine,
    )
    from areal.infra import SlurmScheduler  # type: ignore[import-not-found]
    from areal.infra.rpc.rtensor import RTensor  # type: ignore[import-not-found]

    scheduler = SlurmScheduler(exp_config=config)
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
    # An immutable evaluation stays at version zero; size admission for the full batch.
    config.rollout.consumer_batch_size = len(rows)
    config.rollout.deterministic_sampling = True
    engine = engine_type.as_controller(config.rollout, scheduler)
    try:
        engine.initialize(role="eval-rollout", server_args=server_args)
        for index, data in enumerate(rows):
            engine.submit(
                data,
                workflow=workflow,
                workflow_kwargs=workflow_kwargs,
                task_id=index,
                group_size=1,
                is_eval=True,
            )
        results = engine.wait(len(rows), timeout=None)
        if len(results) != len(rows) or any(result is None for result in results):
            raise RuntimeError("AReaL returned an incomplete evaluation batch")
        return tuple(RTensor.localize(result) for result in results)
    finally:
        engine.destroy()
