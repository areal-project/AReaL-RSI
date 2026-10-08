"""Train TIR on Slurm while Arena runs the registered Harness and tool environment."""

from __future__ import annotations

import argparse
from pathlib import Path

from areal_rsi.api.control import json_bytes, read_record
from areal_rsi.api.execution import ArenaTrainingDataset
from areal_rsi.infra.coordination.arena import arena_get, verify_pins
from areal_rsi.scripts.train import TrainingPlan, import_symbol, load_config


def prepare(argv: list[str]) -> TrainingPlan | None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--training-resources", type=Path, required=True)
    parser.add_argument(
        "--arena-base", default="https://htnn-gateway.alipay.com/arenaonlinegateway"
    )
    parser.add_argument("--validate-only", action="store_true")
    args, areal_args = parser.parse_known_args(argv)
    resources = read_record(args.training_resources, ArenaTrainingDataset)
    config = load_config(areal_args)
    allocation = import_symbol("areal.api.alloc_mode.ModelAllocation").from_str(
        config.actor.backend, name="actor"
    )
    if config.train_dataset.batch_size < allocation.parallel.dp_size:
        raise ValueError("Arena training batch needs at least one trajectory group per actor rank")
    if config.rollout.agent.mode != "inline" or not config.rollout.dump_to_file:
        raise ValueError("Arena GRPO needs an inline Agent with persisted rollout trajectories")
    verify_pins((resources.model, resources.harness, resources.reward, *resources.envs))
    stream = arena_get(f"/streams/{resources.stream_id}")
    stream = stream.get("stream", stream) if stream is not None else None
    if (
        stream is None
        or stream.get("status") != "ACTIVE"
        or stream.get("default_harness_ref") != resources.harness.ref()
        or stream.get("default_reward_ref") != resources.reward.ref()
    ):
        raise ValueError("Arena training Stream is not active")
    rows = tuple(
        {
            "stream_name": "tir",
            "stream_id": resources.stream_id,
            "data_id": f"env:{pin.key}@{pin.version}",
            "llm_protocol": "anthropic",
            "reward_ref_key": resources.reward.key,
            "reward_ref_version": resources.reward.version,
        }
        for pin in resources.envs
    )
    if config.train_dataset.batch_size > len(rows):
        raise ValueError("Training batch is larger than the registered Arena dataset")
    econfig = {
        "arena_base_url": args.arena_base,
        "arena_llm_route_mode": "gateway",
        "arena_result_dump_dir": str(args.training_resources.parent / "arena-rollouts"),
        "arena_streams": [
            {
                "name": "tir",
                "stream_id": resources.stream_id,
                "harness": f"{resources.harness.key}@{resources.harness.version}",
                "llm_protocol": "anthropic",
                "expected_reward_ref": resources.reward.ref(),
                "task_envs": {
                    "ANTHROPIC_BASE_URL": "https://arenaonline-prod.alipay.com/api",
                    "OPENAI_BASE_URL": "https://arenaonline-prod.alipay.com/api",
                    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                    "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(config.gconfig.max_new_tokens),
                },
            }
        ],
    }
    # Validate against the real upstream Agent contract before allocating GPUs.
    workflow = import_symbol("examples.swe.arena_agent.ArenaStreamAgentWorkflow")
    stream_config = import_symbol("examples.swe.arena_config.load_arena_stream_configs")(econfig)[0]
    for row in rows:
        workflow._validate_row_reward_ref(row, stream_config)
        workflow._validate_row_llm_protocol(row["llm_protocol"], stream_config)
    if args.validate_only:
        print(
            json_bytes(
                {
                    "status": "validated",
                    "run_id": resources.run_id,
                    "stream_id": resources.stream_id,
                    "training_rows": len(rows),
                    "harness": resources.harness,
                    "model": resources.model,
                }
            ).decode()
        )
        return None
    # The existing AReaL workflow owns proxy sessions, gateway registrations,
    # Arena launch/poll, model-vs-system failure classification and token export.
    return TrainingPlan(
        config,
        rows,
        None,
        "examples.swe.arena_agent.ArenaStreamAgentWorkflow",
        {
            "econfig": econfig,
            "gen_args": {
                "temperature": config.gconfig.temperature,
                "top_p": config.gconfig.top_p,
                "top_k": config.gconfig.top_k,
                "max_completion_tokens": config.gconfig.max_new_tokens,
            },
        },
    )
