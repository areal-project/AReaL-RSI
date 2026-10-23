"""Compose the Qwen3.5-35B configuration and submit a CPU controller to Slurm.

Only the AReaL scheduler creates GPU jobs. This entry point never launches local
workers and needs only PyYAML on the submission host (already in AReaL runtimes).
"""

from __future__ import annotations

import argparse
import copy
import getpass
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent
ARENA_DATA_KEYS = {
    "arena_streams",
    "arena_streams_file",
    "arena_streams_yaml_b64",
    "arena_mixture_epoch_size",
    "arena_harness",
    "arena_llm_protocol",
    "arena_task_envs",
    "arena_reward_threshold",
    "arena_reward_transform_fn",
    "stream_id",
}
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}\Z")


@dataclass(frozen=True)
class SlurmSettings:
    partition: str
    reservation: str | None
    controller_cpus: int
    controller_mem_gb: int
    controller_time: str
    worker_time: str


@dataclass(frozen=True)
class Runtime:
    areal_dir: str
    actor_image: str
    rollout_image: str
    actor_python: str
    rollout_python: str
    actor_pythonpath: list[str]
    rollout_pythonpath: list[str]
    controller_binds: str
    actor_binds: str
    rollout_binds: str
    slurm: SlurmSettings
    preflight_min_terminal_tasks: int
    preflight_allow_unhealthy: bool = False


def load_yaml(path: Path) -> dict[str, Any]:
    """Reject duplicate keys rather than silently losing a configuration value."""

    class UniqueLoader(yaml.SafeLoader):
        pass

    def mapping(loader: Any, node: Any) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node)
            if not isinstance(key, str) or key in result:
                raise ValueError(f"{path}: duplicate or non-string YAML key")
            result[key] = loader.construct_object(value_node)
        return result

    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    value = yaml.load(path.read_text(), Loader=UniqueLoader)
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a YAML mapping")
    return value


def merge(base: Any, patch: Any) -> Any:
    """Deep merge mappings; the single scheduling_spec item is merged by index."""
    if isinstance(base, dict) and isinstance(patch, dict):
        result = copy.deepcopy(base)
        for key, value in patch.items():
            if key == "scheduling_spec" and key in result:
                if not isinstance(value, list) or len(value) != len(result[key]):
                    raise ValueError("scheduling_spec must retain the base worker spec count")
                result[key] = [merge(old, new) for old, new in zip(result[key], value, strict=True)]
            else:
                result[key] = merge(result[key], value) if key in result else copy.deepcopy(value)
        return result
    return copy.deepcopy(patch)


def protect_base(base: Any, patch: Any, prefix: str = "") -> None:
    """Base owns fixed leaves; params can supply new leaves and Slurm resources."""
    if prefix == "launch.slurm":
        return
    if isinstance(base, dict) and isinstance(patch, dict):
        for key, value in patch.items():
            name = f"{prefix}.{key}" if prefix else key
            if key in base:
                protect_base(base[key], value, name)
    elif prefix.endswith("scheduling_spec") and isinstance(base, list) and isinstance(patch, list):
        if len(base) != len(patch):
            raise ValueError("scheduling_spec must retain the base worker spec count")
        for index, (old, new) in enumerate(zip(base, patch, strict=True)):
            protect_base(old, new, f"{prefix}.{index}")
    else:
        raise ValueError(f"{prefix} is fixed in base.yaml; change the base profile deliberately")


def compose(profile: Path, params_path: Path, arena_path: Path) -> dict[str, Any]:
    base = load_yaml(profile / "base.yaml")
    params = load_yaml(params_path)
    arena = load_yaml(arena_path)
    if "defaults" in params or "defaults" in arena:
        raise ValueError("Hydra defaults are not allowed in override files")
    if set(params.get("econfig", {})) & ARENA_DATA_KEYS:
        raise ValueError("Arena dataset selection belongs in arena.yaml")
    if set(arena) != {"econfig"} or not isinstance(arena["econfig"], dict):
        raise ValueError("arena.yaml must contain only econfig dataset settings")
    if set(arena["econfig"]) - ARENA_DATA_KEYS:
        raise ValueError("arena.yaml may only override Arena dataset settings")
    protect_base(base, params)
    protect_base(base, arena)
    config = merge(merge(base, params), arena)
    # OmegaConf's structured merge rejects whole-dataclass interpolations as
    # strings. Copy these container links after applying actor overrides, while
    # retaining scalar/environment interpolations inside each container.
    for key in ("megatron", "mb_spec", "scheduling_spec"):
        if config["ref"].get(key) == f"${{actor.{key}}}":
            config["ref"][key] = copy.deepcopy(config["actor"][key])
    validate(config)
    return config


def validate(config: dict[str, Any]) -> None:
    actor, rollout, generation = config["actor"], config["rollout"], config["gconfig"]
    megatron = actor["megatron"]
    samples, minimum = generation["n_samples"], actor.get("min_usable_group_size", 2)
    if not isinstance(samples, int) or isinstance(samples, bool) or not 2 <= minimum <= samples:
        raise ValueError("Require 2 <= actor.min_usable_group_size <= gconfig.n_samples")
    if (
        not 0
        < generation["max_new_tokens"]
        <= generation["max_tokens"]
        < config["sglang"]["context_length"]
    ):
        raise ValueError("Require 0 < max_new_tokens <= max_tokens < sglang.context_length")
    if generation["max_tokens"] > actor["mb_spec"]["max_tokens_per_mb"]:
        raise ValueError("actor.mb_spec.max_tokens_per_mb must cover the maximum trajectory length")
    if generation.get("reward_normalization") and actor.get("reward_norm") is not None:
        raise ValueError("Choose rollout reward_normalization OR actor.reward_norm, not both")
    if megatron["cpu_staged_offload"]["enabled"] and (
        actor["optimizer"]["type"] != "adam" or megatron["async_save"]
    ):
        raise ValueError("CPU staging requires optimizer.type=adam and async_save=false")
    if megatron["lm_head_loss_chunk_size"] > 0 and (
        not megatron["enable_chunked_logits"] or megatron["entropy_requires_grad"]
    ):
        raise ValueError(
            "Chunked loss requires enable_chunked_logits=true, entropy_requires_grad=false"
        )
    if actor["optimizer"]["warmup_steps"] is not None and not (
        0 <= actor["optimizer"]["warmup_steps"] < config["total_train_steps"]
    ):
        raise ValueError("Require 0 <= optimizer.warmup_steps < total_train_steps")
    if config["train_dataset"]["batch_size"] < 1 or rollout["max_concurrent_rollouts"] < 1:
        raise ValueError("Batch size and rollout concurrency must be positive")
    if rollout["queue_size"] <= config["train_dataset"]["batch_size"]:
        raise ValueError(
            "rollout.queue_size must exceed train_dataset.batch_size to allow dispatch"
        )
    data = config["econfig"]
    sources = [
        data.get(key)
        for key in ("arena_streams", "arena_streams_file", "arena_streams_yaml_b64", "stream_id")
    ]
    if sum(bool(value) for value in sources) != 1:
        raise ValueError("Select exactly one Arena stream source")
    for stream in data.get("arena_streams", []):
        if not stream.get("stream_id") or not stream.get("harness"):
            raise ValueError("Each Arena stream must pin stream_id and harness")
        reward = stream.get("expected_reward_ref", {})
        if not reward.get("key") or not reward.get("version"):
            raise ValueError("Each Arena stream must pin its expected reward key/version")


def runtime_from_config(config: dict[str, Any], profile: Path, args: argparse.Namespace) -> Runtime:
    raw = copy.deepcopy(config.pop("launch"))
    source = args.areal_dir or os.environ.get("AREAL_DIR") or raw["areal_dir"]
    raw["areal_dir"] = str((profile / source).resolve())
    slurm = raw.pop("slurm")
    if args.partition:
        slurm["partition"] = args.partition
    # Only an explicit CLI flag opts into reserved resources. Inherited env or
    # old profile values must never change the public-pool default.
    slurm["reservation"] = args.reservation
    for key in ("partition", "reservation"):
        if slurm[key] is not None and not SAFE_NAME.fullmatch(slurm[key]):
            raise ValueError(f"Invalid Slurm {key}")
    for key in ("controller_time", "worker_time"):
        if not re.fullmatch(r"(?:\d+-)?\d+:\d{2}:\d{2}", slurm[key]):
            raise ValueError(f"Invalid Slurm {key}; use HH:MM:SS or D-HH:MM:SS")
    for key in ("controller_cpus", "controller_mem_gb"):
        if not isinstance(slurm[key], int) or slurm[key] < 1:
            raise ValueError(f"Slurm {key} must be positive")
    raw["slurm"] = SlurmSettings(**slurm)
    return Runtime(**raw)


def environment(
    runtime: Runtime, run_dir: Path, experiment_name: str, trial_name: str, credentials: str
) -> dict[str, str]:
    values = {
        "AREAL_DIR": runtime.areal_dir,
        "AREAL_ACTOR_IMAGE": runtime.actor_image,
        "AREAL_ROLLOUT_IMAGE": runtime.rollout_image,
        "AREAL_ACTOR_PYTHON": runtime.actor_python,
        "AREAL_ROLLOUT_PYTHON": runtime.rollout_python,
        "AREAL_ACTOR_PYTHONPATH": ":".join([*runtime.actor_pythonpath, runtime.areal_dir]),
        "AREAL_ROLLOUT_PYTHONPATH": ":".join([*runtime.rollout_pythonpath, runtime.areal_dir]),
        "AREAL_CONTROLLER_BINDS": runtime.controller_binds,
        "AREAL_ACTOR_BINDS": runtime.actor_binds,
        "AREAL_ROLLOUT_BINDS": runtime.rollout_binds,
        "CREDENTIALS_FILE": credentials,
        "EXPERIMENT_RUN_DIR": str(run_dir),
        "EXPERIMENT_NAME": experiment_name,
        "EXPERIMENT_TRIAL": trial_name,
        "ARENA_PREFLIGHT_MIN_TASKS": str(runtime.preflight_min_terminal_tasks),
        "ARENA_PREFLIGHT_ALLOW_UNHEALTHY": "1" if runtime.preflight_allow_unhealthy else "0",
        "SBATCH_PARTITION": runtime.slurm.partition,
        "SBATCH_TIMELIMIT": runtime.slurm.worker_time,
        "SLURM_EXPORT_ENV": "ALL",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "NO_PROXY": "*",
        "no_proxy": "*",
    }
    if runtime.slurm.reservation:
        values["EXPERIMENT_RESERVATION"] = runtime.slurm.reservation
        values["SBATCH_RESERVATION"] = runtime.slurm.reservation
    return values


def clean_submission_environment(values: dict[str, str]) -> dict[str, str]:
    result = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("SBATCH_")
        and key
        not in {
            "EXPERIMENT_RESERVATION",
            "AREAL_SPMD_MODE",
            "APPTAINER_BIND",
            "APPTAINER_BINDPATH",
            "SINGULARITY_BIND",
            "SINGULARITY_BINDPATH",
        }
    }
    result.update(values)
    return result


def sbatch_command(
    runtime: Runtime, run_dir: Path, experiment_name: str, trial_name: str, check_only: bool
) -> list[str]:
    settings = runtime.slurm
    command = [
        "sbatch",
        "--parsable",
        "--partition",
        settings.partition,
        "--nodes=1",
        "--ntasks=1",
        "--gres=gpu:0",
        f"--cpus-per-task={settings.controller_cpus}",
        f"--mem={settings.controller_mem_gb}G",
        f"--time={settings.controller_time}",
        "--no-requeue",
        "--signal=B:TERM@120",
        "--export=ALL",
        "--job-name",
        f"{experiment_name}-{trial_name}-controller",
        "--chdir",
        runtime.areal_dir,
        "--output",
        str(run_dir / "controller-%j.log"),
    ]
    if settings.reservation:
        command.extend(["--reservation", settings.reservation])
    if check_only:
        command.append("--wait")
    command.extend([str(run_dir / "controller.sbatch"), str(run_dir)])
    if check_only:
        command.append("--check-only")
    return command


def check_paths(runtime: Runtime, config: dict[str, Any], credentials: str) -> None:
    paths = [runtime.actor_image, runtime.rollout_image, config["actor"]["path"], credentials]
    paths.extend(runtime.actor_pythonpath + runtime.rollout_pythonpath)
    paths.append(str(Path(runtime.areal_dir) / "areal" / "__init__.py"))
    for binds in (runtime.controller_binds, runtime.actor_binds, runtime.rollout_binds):
        paths.extend(item.split(":", 1)[0] for item in binds.split(","))
    for path in paths:
        if not path or not Path(path).exists():
            raise ValueError(
                f"Missing runtime path: {path or 'CREDENTIALS_FILE (set --credentials-file)'}"
            )
    for executable in ("sbatch", "singularity"):
        if shutil.which(executable) is None:
            raise ValueError(f"Required executable not found: {executable}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--params", type=Path)
    parser.add_argument("--arena", type=Path)
    parser.add_argument(
        "--experiment-name", help="Experiment group (default: base.yaml experiment_name)"
    )
    parser.add_argument("--trial-name", default="trial-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    parser.add_argument(
        "--fileroot",
        type=Path,
        help="Shared AReaL log/checkpoint root (default: cluster.fileroot)",
    )
    parser.add_argument("--areal-dir")
    parser.add_argument(
        "--credentials-file",
        default=os.environ.get("CREDENTIALS_FILE") or str(ROOT / "credentials.env"),
        help="Credentials path (default: CREDENTIALS_FILE or script-local credentials.env)",
    )
    parser.add_argument("--partition")
    parser.add_argument("--reservation", help="Explicit reservation; omitted means public pool")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Print composed config; no files or Slurm/Arena calls",
    )
    mode.add_argument(
        "--check-only", action="store_true", help="CPU preflight; remove temporary files on success"
    )
    args = parser.parse_args()
    profile = ROOT
    params_path = (args.params or profile / "params.yaml").resolve()
    arena_path = (args.arena or profile / "arena.yaml").resolve()
    config = compose(profile, params_path, arena_path)
    experiment_name = args.experiment_name or config["experiment_name"]
    trial_name = args.trial_name
    for flag, name in (("--experiment-name", experiment_name), ("--trial-name", trial_name)):
        if not SAFE_NAME.fullmatch(name):
            parser.error(f"{flag} must contain only letters, digits, '.', '_' and '-'")
    config["experiment_name"] = experiment_name
    config["trial_name"] = trial_name
    runtime = runtime_from_config(config, profile, args)
    credentials = (
        str(Path(args.credentials_file).expanduser().resolve()) if args.credentials_file else ""
    )
    fileroot = (args.fileroot or Path(config["cluster"]["fileroot"])).expanduser().resolve()
    config["cluster"]["fileroot"] = str(fileroot)
    log_root = fileroot / "logs" / getpass.getuser()
    run_dir = log_root / experiment_name / trial_name
    if args.dry_run:
        command = sbatch_command(runtime, run_dir, experiment_name, trial_name, False)
        print("Dry run: no files created; no Slurm/Arena calls.")
        print(f"Credentials file: {credentials}")
        print(f"Planned controller command: {shlex.join(command)}")
        print("Composed config:")
        print(yaml.safe_dump(config, sort_keys=False), end="")
        return
    check_paths(runtime, config, credentials)
    if args.check_only:
        checks_root = log_root / ".checks"
        checks_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        run_dir = Path(tempfile.mkdtemp(prefix=f"{experiment_name}-{trial_name}-", dir=checks_root))
        # load_expr_config also writes logs. Keep all preflight side effects
        # under the disposable directory rather than reserving a training trial.
        config["cluster"]["fileroot"] = str(run_dir)
    else:
        run_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    os.chmod(run_dir, 0o700)
    for source, name in (
        (profile / "base.yaml", "base.yaml"),
        (params_path, "params.yaml"),
        (arena_path, "arena.yaml"),
    ):
        shutil.copyfile(source, run_dir / name)
    for name in ("controller.sbatch", "controller.py"):
        shutil.copyfile(ROOT / name, run_dir / name)
    (run_dir / "train.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
    values = environment(runtime, run_dir, experiment_name, trial_name, credentials)
    # Credentials themselves are never sourced or serialized on the submit host.
    resets = (
        "unset "
        + " ".join(
            [
                "SBATCH_RESERVATION",
                "SBATCH_NODELIST",
                "SBATCH_EXCLUDE",
                "EXPERIMENT_RESERVATION",
                "AREAL_SPMD_MODE",
                "APPTAINER_BIND",
                "APPTAINER_BINDPATH",
                "SINGULARITY_BIND",
                "SINGULARITY_BINDPATH",
            ]
        )
        + "\n"
    )
    (run_dir / "environment.sh").write_text(
        resets + "".join(f"export {key}={shlex.quote(value)}\n" for key, value in values.items())
    )
    command = sbatch_command(runtime, run_dir, experiment_name, trial_name, args.check_only)
    manifest = {
        "experiment_name": experiment_name,
        "trial_name": trial_name,
        "runtime": asdict(runtime),
        "command": command,
        "dry_run": args.dry_run,
        "sha256": {
            name: hashlib.sha256((run_dir / name).read_bytes()).hexdigest()
            for name in ("base.yaml", "params.yaml", "arena.yaml", "train.yaml")
        },
    }
    source = Path(runtime.areal_dir)
    for label, arguments in (
        ("source_head", ["rev-parse", "HEAD"]),
        ("source_status", ["status", "--short"]),
    ):
        result = subprocess.run(
            ["git", "-C", str(source), *arguments], capture_output=True, text=True, check=False
        )
        manifest[label] = result.stdout.strip() if result.returncode == 0 else "unavailable"
    diff = subprocess.run(
        ["git", "-C", str(source), "diff", "--binary", "HEAD"], capture_output=True, check=False
    )
    if diff.returncode == 0:
        (run_dir / "source.patch").write_bytes(diff.stdout)
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Run directory: {run_dir}")
    print(f"Experiment: {experiment_name}; trial: {trial_name}")
    print(f"Slurm reservation: {runtime.slurm.reservation or '(public pool)'}")
    print(f"Controller command: {shlex.join(command)}")
    if args.check_only:
        print(
            "Waiting for CPU preflight. Slurm job ID follows; success removes temporary files.",
            flush=True,
        )
        # Wait on the submission host: deleting an open Slurm log from inside
        # its job leaves NFS .nfs files and can make an otherwise successful job fail.
        result = subprocess.run(command, env=clean_submission_environment(values), check=False)
        if result.returncode:
            raise ValueError(f"CPU preflight failed; logs retained in: {run_dir}")
        shutil.rmtree(run_dir)
        print("CPU runtime and Arena preflights PASSED; temporary files removed.")
        return
    result = subprocess.run(
        command,
        env=clean_submission_environment(values),
        capture_output=True,
        text=True,
        check=True,
    )
    (run_dir / "controller.jobid").write_text(result.stdout.strip() + "\n")
    print(f"Controller job: {result.stdout.strip()}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, yaml.YAMLError, subprocess.CalledProcessError) as exc:
        print(f"Submission failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
