"""Container-side checks and training; contains no scheduler implementation."""

from __future__ import annotations

import argparse
import base64
import importlib.metadata
import json
import os
import runpy
import signal
import sys
from dataclasses import asdict
from pathlib import Path

import yaml


def check_source() -> Path:
    import areal

    expected = Path(os.environ["AREAL_DIR"]).resolve()
    if Path(areal.__file__).resolve().parent != expected / "areal":
        raise RuntimeError("Controller/worker PYTHONPATH is loading a different AReaL checkout")
    return expected


def record_runtime(role: str) -> None:
    check_source()
    packages = ("awex", "transformers", "torch", "sglang", "megatron-core", "megatron-bridge")
    versions = {}
    for package in packages:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not installed"
    root = Path(os.environ["EXPERIMENT_RUN_DIR"])
    (root / f"runtime-{role}.json").write_text(json.dumps(versions, indent=2) + "\n")
    print(f"{role} runtime: {json.dumps(versions)}", flush=True)


def rollout_runtime() -> None:
    record_runtime("rollout")
    from areal.engine.awex.plugins.sglang_plugin import assert_supported_sglang_version

    assert_supported_sglang_version()
    # Require the actual Qwen3.5 converter, not just an AWEX version label.
    from awex.models import qwen3_5
    from sglang.srt.server_args import ServerArgs

    assert qwen3_5.__file__
    assert ServerArgs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rollout-runtime", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if args.rollout_runtime:
        rollout_runtime()
        return
    record_runtime("actor")
    # Binds belong to the outer worker containers, not the controller container.
    for name in (
        "APPTAINER_BIND",
        "APPTAINER_BINDPATH",
        "SINGULARITY_BIND",
        "SINGULARITY_BINDPATH",
    ):
        os.environ.pop(name, None)
    os.environ.pop("AREAL_SPMD_MODE", None)
    os.environ["PYTHONPATH"] = os.environ["AREAL_ACTOR_PYTHONPATH"]

    from areal.api.cli_args import load_expr_config
    from areal.engine.core.model import validate_model_packed_seq_dependencies
    from examples.swe.arena_config import load_arena_stream_configs
    from examples.swe.arena_preflight import validate_streams
    from examples.swe.utils import SWEPPOConfig

    root = Path(os.environ["EXPERIMENT_RUN_DIR"])
    config_path = root / "train.yaml"
    config, _ = load_expr_config(["--config", str(config_path)], SWEPPOConfig)
    # CPU checks fail before any GPU worker jobs are submitted.
    validate_model_packed_seq_dependencies("qwen3_5_moe", "megatron-bridge", 4)
    from awex.models import qwen3_5

    assert qwen3_5.__file__
    streams = load_arena_stream_configs(config.econfig)
    payload = yaml.safe_dump({"streams": [asdict(stream) for stream in streams]})
    summary = validate_streams(
        streams_yaml_b64=base64.b64encode(payload.encode()).decode(),
        base_url=config.econfig.arena_base_url,
        min_terminal_tasks=int(os.environ["ARENA_PREFLIGHT_MIN_TASKS"]),
        allow_unhealthy=os.environ.get("ARENA_PREFLIGHT_ALLOW_UNHEALTHY", "0") == "1",
    )
    (root / "preflight.json").write_text(json.dumps({"streams": summary}, indent=2) + "\n")
    for stream in summary:
        if stream.get("unhealthy_override"):
            print(
                "Arena historical health override: "
                f"stream={stream['stream_id']}, "
                f"terminal={stream['recent_terminal_tasks']}, "
                f"failures={stream['recent_failures']}",
                flush=True,
            )
    if args.check_only:
        print("Runtime and Arena preflights passed; no GPU workers submitted.", flush=True)
        return

    def terminate(signum: int, _frame: object) -> None:
        # Unwind trainer contexts so the Slurm scheduler destroys owned workers.
        raise KeyboardInterrupt(f"Controller received signal {signum}")

    signal.signal(signal.SIGTERM, terminate)
    sys.argv = ["examples.swe.train_swe_rl", "--config", str(config_path)]
    runpy.run_module("examples.swe.train_swe_rl", run_name="__main__")


if __name__ == "__main__":
    main()
