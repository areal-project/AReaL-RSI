"""Submit one Qwen3.5 SFT Slurm allocation running eight torchrun ranks."""

from __future__ import annotations

import argparse
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

import yaml

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
from profile_config import (  # noqa: E402
    SAFE_NAME,
    load_yaml,
    merge,
    protect_base,
    resolve_areal_dir,
)


@dataclass(frozen=True)
class SlurmSettings:
    partition: str
    nodes: int
    gpus_per_node: int
    cpus_per_task: int
    mem_gb: int
    time: str
    reservation: str | None


@dataclass(frozen=True)
class Runtime:
    areal_dir: str
    image: str
    python: str
    pythonpath: list[str]
    binds: str
    env: dict[str, str]
    slurm: SlurmSettings


def compose(params_path: Path, data_path: Path) -> dict:
    base, params, data = (load_yaml(p) for p in (ROOT / "base.yaml", params_path, data_path))
    if any("defaults" in x for x in (params, data)):
        raise ValueError("Hydra defaults are not supported")
    if set(data) - {"train_dataset", "valid_dataset"}:
        raise ValueError("data.yaml contains only train_dataset and valid_dataset")
    for section in ("train_dataset", "valid_dataset"):
        if isinstance(params.get(section), dict) and set(params[section]) & {"path", "split"}:
            raise ValueError("Dataset paths and splits belong in data.yaml")
        if data.get(section) is not None and (
            not isinstance(data[section], dict) or set(data[section]) - {"path", "split"}
        ):
            raise ValueError("data.yaml may only select paths and splits")
    protect_base(base, params)
    protect_base(base, data)
    config = merge(merge(base, params), data)
    if config.get("valid_dataset") is not None:
        config["valid_dataset"] = merge(
            {
                "type": "sft",
                "batch_size": config["train_dataset"]["batch_size"],
                "max_length": config["train_dataset"]["max_length"],
                "shuffle": False,
                "pin_memory": True,
                "num_workers": 0,
                "drop_last": False,
                "scheduling_spec": None,
            },
            config["valid_dataset"],
        )
    for section in ("train_dataset", "valid_dataset"):
        if config.get(section) is not None:
            source = config[section].get("path")
            if not isinstance(source, str) or not source.strip():
                raise ValueError(f"{section}.path must select a text JSONL file")
            config[section]["path"] = str((data_path.parent / source).expanduser().resolve())
    actor = config["actor"]
    batch, mb = config["train_dataset"]["batch_size"], actor["mb_spec"]
    if type(batch) is not int or batch < 2 or batch % 2:
        raise ValueError("Global batch size must be positive and divisible by attention DP2")
    n_mbs = mb.get("n_mbs", 1)  # AReaL MicroBatchSpec default when omitted.
    if type(n_mbs) is not int or not 1 <= n_mbs <= batch // 2:
        raise ValueError("Require 1 <= n_mbs <= batch_size / DP2")
    if not 0 < config["train_dataset"]["max_length"] <= mb["max_tokens_per_mb"]:
        raise ValueError("max_tokens_per_mb must cover one maximum-length sample")
    if actor["optimizer"]["eps"] <= 0 or actor["optimizer"]["lr"] <= 0:
        raise ValueError("Optimizer lr and eps must be positive")
    if actor["megatron"]["cpu_staged_offload"]["enabled"] and (
        actor["optimizer"]["type"] != "adam" or actor["megatron"]["async_save"]
    ):
        raise ValueError("CPU staging requires Adam and synchronous checkpointing")
    if actor["megatron"]["lm_head_loss_chunk_size"] > 0 and (
        not actor["megatron"]["enable_chunked_logits"] or actor["megatron"]["entropy_requires_grad"]
    ):
        raise ValueError("Chunked loss requires chunked logits and no entropy gradients")
    if config["total_train_epochs"] < 1:
        raise ValueError("total_train_epochs must be positive")
    if config["total_train_steps"] is not None and config["total_train_steps"] < 1:
        raise ValueError("total_train_steps must be positive or null")
    return config


def runtime_from_config(config: dict, args: argparse.Namespace, credentials: str = "") -> Runtime:
    raw = config.pop("launch")
    if "areal_dir" in raw:
        raise ValueError("Move launch.areal_dir out of YAML and set AREAL_DIR instead")
    raw["areal_dir"] = resolve_areal_dir(credentials)
    settings = raw.pop("slurm")
    settings.update(partition=args.partition or settings["partition"], reservation=args.reservation)
    for key in ("partition", "reservation"):
        if settings[key] is not None and not SAFE_NAME.fullmatch(settings[key]):
            raise ValueError(f"Invalid Slurm {key}")
    for key in ("cpus_per_task", "mem_gb"):
        if type(settings[key]) is not int or settings[key] < 1:
            raise ValueError(f"Slurm {key} must be positive")
    if not re.fullmatch(r"(?:\d+-)?\d+:\d{2}:\d{2}", settings["time"]):
        raise ValueError("Slurm time must use HH:MM:SS or D-HH:MM:SS")
    raw["slurm"] = SlurmSettings(**settings)
    return Runtime(**raw)


def credentials_file(explicit: str | None) -> str:
    if value := explicit or os.environ.get("CREDENTIALS_FILE"):
        return str(Path(value).expanduser().resolve())
    for path in (ROOT / "credentials.env", ROOT.parent / "credentials.env"):
        if path.is_file():
            return str(path)
    return ""


def environment(runtime: Runtime, root: Path, check_only: bool, credentials: str) -> dict[str, str]:
    return {
        **runtime.env,
        "AREAL_DIR": runtime.areal_dir,
        "SFT_IMAGE": runtime.image,
        "SFT_PYTHON": runtime.python,
        "SFT_PYTHONPATH": ":".join([*runtime.pythonpath, runtime.areal_dir]),
        "SFT_BINDS": runtime.binds,
        "SFT_RUN_DIR": str(root),
        "SFT_CPUS": str(
            min(8, runtime.slurm.cpus_per_task) if check_only else runtime.slurm.cpus_per_task
        ),
        "CREDENTIALS_FILE": credentials,
        "EXPERIMENT_TRIAL": root.name,
        "USER": getpass.getuser(),
        "LOGNAME": getpass.getuser(),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "SLURM_EXPORT_ENV": "ALL",
    }


def sbatch_command(
    runtime: Runtime, root: Path, experiment: str, trial: str, check_only: bool
) -> list[str]:
    s = runtime.slurm
    command = [
        "sbatch",
        "--parsable",
        "--partition",
        s.partition,
        "--nodes=1",
        "--ntasks=1",
        f"--gres=gpu:{0 if check_only else s.gpus_per_node}",
        f"--cpus-per-task={min(8, s.cpus_per_task) if check_only else s.cpus_per_task}",
        f"--mem={min(64, s.mem_gb) if check_only else s.mem_gb}G",
        f"--time={s.time}",
        "--no-requeue",
        "--signal=B:TERM@120",
        "--export=ALL",
        "--job-name",
        f"{experiment}-{trial}-{'check' if check_only else 'sft'}",
        "--chdir",
        runtime.areal_dir,
        "--output",
        str(root / "train-%j.log"),
    ]
    if s.reservation:
        command.extend(["--reservation", s.reservation])
    if check_only:
        command.append("--wait")
    command.extend([str(root / "train.sbatch"), str(root)])
    if check_only:
        command.append("--check-only")
    return command


def check_paths(runtime: Runtime, config: dict, credentials: str) -> None:
    paths = [runtime.image, config["actor"]["path"], *runtime.pythonpath]
    paths.append(str(Path(runtime.areal_dir) / "areal/trainer/sft_trainer.py"))
    paths.extend(item.split(":", 1)[0] for item in runtime.binds.split(","))
    paths.extend(
        config[key]["path"] for key in ("train_dataset", "valid_dataset") if config.get(key)
    )
    if credentials:
        paths.append(credentials)
    for value in paths:
        if not Path(value).exists():
            raise ValueError(f"Missing runtime/data path: {value}")
    for name in ("sbatch", "singularity"):
        if shutil.which(name) is None:
            raise ValueError(f"Required executable not found: {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--params", type=Path, default=ROOT / "params.yaml")
    parser.add_argument("--data", type=Path, default=ROOT / "data.yaml")
    parser.add_argument("--experiment-name")
    parser.add_argument("--trial-name", default="trial-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    parser.add_argument("--credentials-file")
    parser.add_argument("--partition")
    parser.add_argument("--reservation", help="Explicit reservation; omitted means public pool")
    parser.add_argument("--fileroot", type=Path)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--dry-run", action="store_true", help="Compose only; no files or Slurm calls"
    )
    modes.add_argument(
        "--check-only", action="store_true", help="Wait for a temporary CPU/data preflight"
    )
    args = parser.parse_args()
    config = compose(args.params.resolve(), args.data.resolve())
    experiment, trial = args.experiment_name or config["experiment_name"], args.trial_name
    for value in (experiment, trial):
        if not SAFE_NAME.fullmatch(value):
            raise ValueError("Experiment/trial names allow only letters, digits, '.', '_' and '-'")
    config.update(experiment_name=experiment, trial_name=trial)
    credentials = credentials_file(args.credentials_file)
    runtime = runtime_from_config(config, args, credentials)
    fileroot = (args.fileroot or Path(config["cluster"]["fileroot"])).expanduser().resolve()
    config["cluster"]["fileroot"] = str(fileroot)
    root = fileroot / "logs" / getpass.getuser() / experiment / trial
    if args.dry_run:
        print("Dry run: no files created; no Slurm or network calls.")
        print(
            "Planned command: "
            + shlex.join(sbatch_command(runtime, root, experiment, trial, False))
        )
        print("Composed config:")
        print(yaml.safe_dump(config, sort_keys=False), end="")
        return
    check_paths(runtime, config, credentials)
    if args.check_only:
        checks = fileroot / "logs" / getpass.getuser() / ".checks"
        checks.mkdir(parents=True, exist_ok=True, mode=0o700)
        root = Path(tempfile.mkdtemp(prefix=f"{experiment}-{trial}-", dir=checks))
        config["cluster"]["fileroot"] = str(root)
    else:
        root.mkdir(parents=True, exist_ok=False, mode=0o700)
    for source, name in (
        (ROOT / "base.yaml", "base.yaml"),
        (args.params, "params.yaml"),
        (args.data, "data.yaml"),
    ):
        shutil.copyfile(source, root / name)
    for name in ("train.sbatch", "worker.sh", "train.py"):
        shutil.copyfile(ROOT / name, root / name)
    (root / "train.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
    values = environment(runtime, root, args.check_only, credentials)
    resets = "unset SBATCH_RESERVATION SBATCH_NODELIST SBATCH_EXCLUDE EXPERIMENT_RESERVATION\n"
    (root / "environment.sh").write_text(
        resets + "".join(f"export {k}={shlex.quote(v)}\n" for k, v in values.items())
    )
    manifest = {
        "experiment_name": experiment,
        "trial_name": trial,
        "runtime": asdict(runtime),
        "sha256": {
            n: hashlib.sha256((root / n).read_bytes()).hexdigest()
            for n in ("base.yaml", "params.yaml", "data.yaml", "train.yaml", "train.py")
        },
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    command = sbatch_command(runtime, root, experiment, trial, args.check_only)
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("SBATCH_", "SINGULARITYENV_", "APPTAINERENV_"))
    }
    env.update(values)
    print(f"Run directory: {root}")
    print(f"Experiment: {experiment}; trial: {trial}")
    print(f"Slurm reservation: {runtime.slurm.reservation or '(public pool)'}")
    print("Command: " + shlex.join(command), flush=True)
    result = subprocess.run(
        command, env=env, check=False, text=True, capture_output=not args.check_only
    )
    if result.returncode:
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        raise ValueError(f"Slurm submission/preflight failed; logs retained in {root}")
    if args.check_only:
        shutil.rmtree(root)
        print("SFT CPU runtime and dataset preflight PASSED; temporary files removed.")
    else:
        (root / "train.jobid").write_text(result.stdout.strip() + "\n")
        print("Training job: " + result.stdout.strip())


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, yaml.YAMLError) as exc:
        print(f"Submission failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
