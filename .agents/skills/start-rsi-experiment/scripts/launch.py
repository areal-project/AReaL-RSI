#!/usr/bin/env python3
"""Resolve one isolated RSI launch configuration and optionally start it."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


EXPERIMENT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare an isolated AReaL-RSI config and launch it only with --launch."
    )
    parser.add_argument("--repo-root", type=Path, help="AReaL-RSI repository root.")
    parser.add_argument("--base-config", default="config/rsi.yaml")
    parser.add_argument("--experiment-name", required=True)
    parser.add_argument("--initial-ckpt", required=True)
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--holdout-benchmark", required=True)
    parser.add_argument("--harness", required=True)
    parser.add_argument("--max-iterations", required=True, type=int)
    parser.add_argument("--target-mean-reward", required=True, type=float)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--startup-timeout", type=float, default=30.0)
    parser.add_argument(
        "--launch",
        action="store_true",
        help="Start the controller in a detached process after preparing configs.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = (args.repo_root or Path(__file__).resolve().parents[4]).resolve()
    _validate_args(args, repo_root)
    loaded_env = _load_dotenv(repo_root / ".env")

    base_path = _resolve(repo_root, Path(args.base_config))
    base = _load_yaml_mapping(base_path)
    output_dir = _launch_dir(repo_root, args)
    output_dir.mkdir(parents=True, exist_ok=False)

    resolved_areal_path = output_dir / "areal-training.yaml"
    resolved_rsi_path = output_dir / "rsi.yaml"
    resolved = _resolve_rsi_config(
        base,
        base_path=base_path,
        resolved_areal_path=resolved_areal_path,
        experiment_name=args.experiment_name,
        initial_ckpt=args.initial_ckpt,
        benchmark=args.benchmark,
        holdout_benchmark=args.holdout_benchmark,
        harness=args.harness,
        max_iterations=args.max_iterations,
        target_mean_reward=args.target_mean_reward,
    )
    _write_yaml(resolved_rsi_path, resolved)

    receipt: dict[str, Any] = {
        "schema_version": "rsi-launch-receipt.v1",
        "launched": False,
        "experiment_name": args.experiment_name,
        "base_config_path": str(base_path),
        "resolved_rsi_config_path": str(resolved_rsi_path),
        "resolved_areal_config_path": str(resolved_areal_path),
        "env_file": str(repo_root / ".env"),
        "envarena_api_token_configured": bool(
            os.environ.get("ENVARENA_API_TOKEN") or "ENVARENA_API_TOKEN" in loaded_env
        ),
    }
    if args.launch:
        receipt.update(
            _launch_controller(
                repo_root=repo_root,
                resolved_rsi_path=resolved_rsi_path,
                output_dir=output_dir,
                run_root=Path(str(resolved["run"]["run_root"])),
                startup_timeout=args.startup_timeout,
            )
        )
    _write_json(output_dir / "launch.json", receipt)
    print(json.dumps(receipt, indent=2, ensure_ascii=False))
    return 0


def _validate_args(args: argparse.Namespace, repo_root: Path) -> None:
    if not EXPERIMENT_NAME.fullmatch(args.experiment_name):
        raise SystemExit(
            "--experiment-name must start with an alphanumeric character and use only "
            "letters, numbers, '.', '_' or '-'."
        )
    if args.max_iterations < 1:
        raise SystemExit("--max-iterations must be >= 1.")
    for name in ("initial_ckpt", "benchmark", "holdout_benchmark", "harness"):
        if not str(getattr(args, name)).strip():
            raise SystemExit(f"--{name.replace('_', '-')} must not be empty.")
    if not (repo_root / "pyproject.toml").is_file():
        raise SystemExit(f"Not an AReaL-RSI repository root: {repo_root}")


def _resolve_rsi_config(
    config: dict[str, Any],
    *,
    base_path: Path,
    resolved_areal_path: Path,
    experiment_name: str,
    initial_ckpt: str,
    benchmark: str,
    holdout_benchmark: str,
    harness: str,
    max_iterations: int,
    target_mean_reward: float,
) -> dict[str, Any]:
    run = _mapping(config, "run")
    env = _mapping(config, "env")
    services = _mapping(config, "services")
    train = _mapping(config, "train")
    train_config = _mapping(train, "config")
    stop_criteria = _mapping(config, "stop_criteria")

    if "run_root" not in run:
        raise SystemExit("Base config requires fixed run.run_root.")
    run.update(
        {
            "initial_ckpt": initial_ckpt,
            "method": "sft",
            "max_iterations": max_iterations,
        }
    )
    env.update(
        {
            "benchmark": benchmark,
            "holdout_benchmark": holdout_benchmark,
            "harness_config": {"harness": harness},
        }
    )
    stop_criteria["target_mean_reward"] = target_mean_reward

    for service_name in ("areal", "envarena"):
        service = services.get(service_name)
        if isinstance(service, dict) and service.get("config_path"):
            service["config_path"] = str(
                _resolve(base_path.parent, Path(str(service["config_path"])))
            )

    template_value = train_config.get("train_config_path") or train_config.get(
        "experiment_config"
    )
    if not template_value:
        raise SystemExit("Base config requires train.config.train_config_path.")
    template_path = _resolve(base_path.parent, Path(str(template_value)))
    template = _load_yaml_mapping(template_path)
    _resolve_areal_template(
        template,
        experiment_name=experiment_name,
        initial_ckpt=initial_ckpt,
    )
    _write_yaml(resolved_areal_path, template)
    train_config["train_config_path"] = str(resolved_areal_path.resolve())
    train_config.pop("experiment_config", None)
    return config


def _resolve_areal_template(
    config: dict[str, Any], *, experiment_name: str, initial_ckpt: str
) -> None:
    config["experiment_name"] = experiment_name
    actor = _mapping(config, "actor")
    actor["path"] = initial_ckpt
    cluster = _mapping(config, "cluster")
    fileroot = str(cluster.get("fileroot") or "").rstrip("/")
    if not fileroot:
        raise SystemExit("AReaL template requires cluster.fileroot.")
    name_resolve = _mapping(cluster, "name_resolve")
    name_resolve["nfs_record_root"] = (
        f"{fileroot}/name_resolve/${{experiment_name}}"
    )


def _launch_controller(
    *,
    repo_root: Path,
    resolved_rsi_path: Path,
    output_dir: Path,
    run_root: Path,
    startup_timeout: float,
) -> dict[str, Any]:
    log_path = output_dir / "controller.log"
    before = _run_directories(run_root)
    command = [
        sys.executable,
        "-m",
        "areal_rsi.cli.main",
        "run",
        "--config",
        str(resolved_rsi_path),
    ]
    with log_path.open("ab", buffering=0) as log:
        process = subprocess.Popen(
            command,
            cwd=repo_root,
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    run_dir: Path | None = None
    deadline = time.monotonic() + max(startup_timeout, 0.0)
    while time.monotonic() < deadline:
        new_dirs = _run_directories(run_root) - before
        if new_dirs:
            run_dir = max(new_dirs, key=lambda path: path.name)
            break
        return_code = process.poll()
        if return_code is not None:
            if return_code != 0:
                tail = _log_tail(log_path)
                raise SystemExit(
                    f"RSI controller exited with code {return_code} before creating a Run. "
                    f"Log: {log_path}\n{tail}"
                )
            break
        time.sleep(0.25)

    return {
        "launched": True,
        "pid": process.pid,
        "run_id": run_dir.name if run_dir else None,
        "run_dir": str(run_dir) if run_dir else None,
        "controller_log_path": str(log_path),
        "command": command,
    }


def _launch_dir(repo_root: Path, args: argparse.Namespace) -> Path:
    if args.output_dir is not None:
        return _resolve(repo_root, args.output_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return repo_root / ".rsi" / "launches" / f"{args.experiment_name}-{stamp}"


def _run_directories(run_root: Path) -> set[Path]:
    root = run_root.expanduser()
    if not root.is_dir():
        return set()
    return {path.resolve() for path in root.glob("run_*") if path.is_dir()}


def _load_dotenv(path: Path) -> set[str]:
    loaded: set[str] = set()
    if not path.is_file():
        return loaded
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise SystemExit(f"Invalid .env entry on line {line_number}.")
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise SystemExit(f"Invalid .env key on line {line_number}.")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)
        loaded.add(key)
    return loaded


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"Config file does not exist: {path}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"Config must contain a YAML mapping: {path}")
    return payload


def _mapping(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.setdefault(key, {})
    if not isinstance(value, dict):
        raise SystemExit(f"Config section {key!r} must be a mapping.")
    return value


def _resolve(parent: Path, path: Path) -> Path:
    expanded = path.expanduser()
    return expanded.resolve() if expanded.is_absolute() else (parent / expanded).resolve()


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _log_tail(path: Path, line_count: int = 20) -> str:
    if not path.is_file():
        return ""
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-line_count:])


if __name__ == "__main__":
    raise SystemExit(main())
