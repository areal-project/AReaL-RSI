"""Container-side checks and training; contains no scheduler implementation."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import operator
import os
import runpy
import signal
import sys
from pathlib import Path


def register_yaml_resolvers() -> None:
    from omegaconf import OmegaConf

    # OmegaConf provides references, but arithmetic requires a named resolver.
    # Keep formulas in YAML and avoid an unrestricted eval resolver.
    OmegaConf.register_new_resolver("mul", operator.mul, replace=True)


def validate_sglang_capacity(config) -> None:
    for key in ("max_running_requests", "cuda_graph_max_bs"):
        value = getattr(config.sglang, key)
        if type(value) is not int or value < 1:
            raise ValueError(f"sglang.{key} must resolve to a positive integer")


def check_source() -> Path:
    import areal

    expected = Path(os.environ["AREAL_DIR"]).resolve()
    if Path(areal.__file__).resolve().parent != expected / "areal":
        raise RuntimeError("Controller/worker PYTHONPATH is loading a different AReaL checkout")
    return expected


def record_runtime(role: str, extra: dict[str, str] | None = None) -> None:
    check_source()
    packages = (
        "awex",
        "transformers",
        "torch",
        "sglang",
        "megatron-core",
        "megatron-bridge",
        "flashinfer-python",
        "flashinfer-cubin",
        "torch-memory-saver",
    )
    versions = {}
    for package in packages:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not installed"
    versions.update(extra or {})
    root = Path(os.environ["EXPERIMENT_RUN_DIR"])
    (root / f"runtime-{role}.json").write_text(json.dumps(versions, indent=2) + "\n")
    print(f"{role} runtime: {json.dumps(versions)}", flush=True)


def rollout_runtime() -> None:
    check_source()
    from areal.engine._sglang_source_patch import installed_sglang_root
    from areal.engine.awex.plugins.sglang_plugin import assert_supported_sglang_version
    from areal.engine.sglang_gdn_decode_padding import TARGET_RELATIVE, patched_source
    from areal.engine.sglang_model_patches import apply_model_patches
    from omegaconf import OmegaConf

    assert_supported_sglang_version()
    # Require the actual Qwen3.5 converter, not just an AWEX version label.
    from awex.models import qwen3_5
    from sglang.srt.server_args import ServerArgs

    assert qwen3_5.__file__
    assert ServerArgs
    root = Path(os.environ["EXPERIMENT_RUN_DIR"])
    config = OmegaConf.load(root / "train.yaml")
    # Exercise the same automatic bootstrap as real workers. This container's
    # temporary writable layer is discarded; workers apply the fix independently.
    apply_model_patches(["--model-path", str(config.actor.path)])
    target = installed_sglang_root() / TARGET_RELATIVE
    source = target.read_bytes()
    if patched_source(source) != source:
        raise RuntimeError("AReaL startup did not apply the required GDN decode padding fix")
    record_runtime(
        "rollout",
        {
            "gdn_decode_padding": "applied and verified",
            "gdn_source": str(target),
            "gdn_sha256": hashlib.sha256(source).hexdigest(),
        },
    )


def run_training(worker_startup_timeout: int) -> None:
    """Supply the native Slurm scheduler with this experiment's queue budget."""
    import areal
    from areal.infra.scheduler.slurm import SlurmScheduler

    original_trainer = areal.PPOTrainer

    # Use the same scheduler factory hook as AReaL's Qwen recipe. The generic
    # SWE entry imports areal.PPOTrainer when main runs; its workflow stays intact.
    class ExperimentTrainer(original_trainer):
        def _init_scheduler(self):
            return SlurmScheduler(exp_config=self.config, startup_timeout=worker_startup_timeout)

    areal.PPOTrainer = ExperimentTrainer
    try:
        print(f"Slurm worker startup timeout: {worker_startup_timeout}s", flush=True)
        runpy.run_path(
            str(Path(os.environ["EXPERIMENT_RUN_DIR"]) / "train.py"), run_name="__main__"
        )
    finally:
        areal.PPOTrainer = original_trainer


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rollout-runtime", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    register_yaml_resolvers()
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
    from train import CodeVPPOConfig, prepare_dataset

    root = Path(os.environ["EXPERIMENT_RUN_DIR"])
    config_path = root / "train.yaml"
    config, _ = load_expr_config(["--config", str(config_path)], CodeVPPOConfig)
    validate_sglang_capacity(config)
    # CPU checks fail before any GPU worker jobs are submitted.
    validate_model_packed_seq_dependencies("qwen3_5_moe", "megatron-bridge", 4)
    from awex.models import qwen3_5

    assert qwen3_5.__file__
    summary = prepare_dataset(config, root)
    (root / "preflight.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"CodeV public dataset validated: {summary['count']} exact Case versions", flush=True)
    if args.check_only:
        print("Runtime and Arena preflights passed; no GPU workers submitted.", flush=True)
        return

    def terminate(signum: int, _frame: object) -> None:
        # Unwind trainer contexts so the Slurm scheduler destroys owned workers.
        raise KeyboardInterrupt(f"Controller received signal {signum}")

    signal.signal(signal.SIGTERM, terminate)
    sys.argv = ["train.py", "--config", str(config_path)]
    run_training(int(os.environ["AREAL_WORKER_STARTUP_TIMEOUT"]))


if __name__ == "__main__":
    main()
