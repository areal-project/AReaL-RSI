"""Launcher for AReaL Experience OPD training (Slurm or local).

The Experience OPD recipes drive AReaL's own scheduler from a single entrypoint
invoked as ``<command> --config <yaml> key=value ...``. This wrapper prepares
the per-iteration overrides (base checkpoint -> actor.path, experience map, and
train/valid data, plus experiment/trial names) and launches that entrypoint.

Two schedulers:

* ``slurm`` (default): render an sbatch that runs the entrypoint inside the
  configured container (one allocation; the recipe's own scheduler manages the
  GPUs within it), submit with ``sbatch --parsable``, then block until the job
  leaves the queue and a checkpoint exists. Completion is detected with
  ``squeue`` only, so it does not depend on ``sacct`` (absent on some clusters).
* ``local``: run the entrypoint in-process and block. Useful for a bare GPU box.

Either way ``train`` returns a completed ``TrainRun`` with the resolved
checkpoint, so the RSI controller treats OPD like any other blocking train step.
"""

from __future__ import annotations

import getpass
import os
import re
import secrets
import shlex
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from areal_rsi.schemas.core import TrainRun

ProcessRunner = Any  # Callable[..., subprocess.CompletedProcess[str]]; kept loose for tests.

_TERMINAL_SLURM_STATES = {
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "TIMEOUT",
    "NODE_FAIL",
    "OUT_OF_MEMORY",
    "BOOT_FAIL",
    "DEADLINE",
    "PREEMPTED",
}


class AReaLOPDConfigurationError(ValueError):
    """Raised when the areal.opd service config is missing or malformed."""


class AReaLOPDTrainingError(RuntimeError):
    """Raised when an OPD launch exits non-zero or yields no checkpoint."""


@dataclass(frozen=True, slots=True)
class AReaLOPDConfig:
    """How to launch an AReaL Experience OPD recipe.

    ``command`` is the entrypoint up to (but excluding) ``--config <yaml>``;
    ``config_path`` is the AReaL experience YAML (absolute, or relative to
    ``working_directory``). ``fileroot``/``experiment_name`` must match the
    YAML's ``cluster.fileroot``/``experiment_name`` so saved checkpoints can be
    resolved. Slurm launches additionally need ``image`` and container/resource
    settings; per-iteration overrides are appended as ``key=value`` args.
    """

    working_directory: Path
    command: tuple[str, ...]
    config_path: str
    fileroot: Path
    experiment_name: str
    trial_name: str = "rsi"
    scheduler_type: str = "slurm"
    environment_variables: Mapping[str, str] = field(default_factory=dict)
    # Slurm-only settings.
    image: str | None = None
    reservation: str | None = None
    partition: str | None = None
    n_nodes: int = 1
    n_gpus_per_node: int = 8
    cpus_per_task: int = 32
    memory: str = "400G"
    time_limit: str = "24:00:00"
    bind_paths: tuple[str, ...] = ("/storage",)
    sbatch_command: tuple[str, ...] = ("sbatch", "--parsable")
    srun_command: tuple[str, ...] = ("srun", "--mpi=none")
    singularity_command: tuple[str, ...] = ("singularity", "exec")
    poll_interval_seconds: float = 30.0
    job_timeout_seconds: float = 604800.0

    @classmethod
    def from_yaml(cls, path: str | Path) -> "AReaLOPDConfig":
        config_path = Path(path)
        try:
            payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except OSError as error:
            raise AReaLOPDConfigurationError(
                f"Unable to read AReaL OPD service config: {config_path}"
            ) from error
        except yaml.YAMLError as error:
            raise AReaLOPDConfigurationError(
                f"AReaL OPD service config is not valid YAML: {config_path}"
            ) from error
        areal = payload.get("areal") if isinstance(payload, Mapping) else None
        section = areal.get("opd") if isinstance(areal, Mapping) else None
        if not isinstance(section, Mapping):
            raise AReaLOPDConfigurationError(
                "OPD service config must contain an 'areal.opd' YAML mapping."
            )
        allowed = {
            "working_directory", "command", "config_path", "fileroot",
            "experiment_name", "trial_name", "scheduler_type", "environment_variables",
            "image", "reservation", "partition", "n_nodes", "n_gpus_per_node",
            "cpus_per_task", "memory", "time_limit", "bind_paths", "sbatch_command",
            "srun_command", "singularity_command", "poll_interval_seconds",
            "job_timeout_seconds",
        }
        unknown = set(section) - allowed
        if unknown:
            raise AReaLOPDConfigurationError(
                f"Unsupported areal.opd fields: {', '.join(sorted(map(str, unknown)))}."
            )
        for required in ("working_directory", "command", "config_path", "fileroot", "experiment_name"):
            if not section.get(required):
                raise AReaLOPDConfigurationError(
                    f"areal.opd.{required} is required and must be non-empty."
                )
        kwargs: dict[str, Any] = dict(
            working_directory=Path(str(section["working_directory"])).expanduser(),
            command=_string_tuple(section["command"], "areal.opd.command"),
            config_path=str(section["config_path"]),
            fileroot=Path(str(section["fileroot"])).expanduser(),
            experiment_name=str(section["experiment_name"]),
            trial_name=str(section.get("trial_name", "rsi")),
            scheduler_type=str(section.get("scheduler_type", "slurm")),
            environment_variables=_string_mapping(
                section.get("environment_variables", {}), "areal.opd.environment_variables"
            ),
        )
        for key in ("image", "reservation", "partition", "memory", "time_limit"):
            if section.get(key) is not None:
                kwargs[key] = str(section[key])
        for key in ("n_nodes", "n_gpus_per_node", "cpus_per_task"):
            if section.get(key) is not None:
                kwargs[key] = int(section[key])
        for key in ("poll_interval_seconds", "job_timeout_seconds"):
            if section.get(key) is not None:
                kwargs[key] = float(section[key])
        for key in ("bind_paths", "sbatch_command", "srun_command", "singularity_command"):
            if section.get(key) is not None:
                kwargs[key] = _string_tuple(section[key], f"areal.opd.{key}")
        return cls(**kwargs)

    def __post_init__(self) -> None:
        if self.scheduler_type not in {"slurm", "local"}:
            raise AReaLOPDConfigurationError(
                "areal.opd.scheduler_type must be 'slurm' or 'local'."
            )
        if not self.command:
            raise AReaLOPDConfigurationError("areal.opd.command must be non-empty.")
        if self.scheduler_type == "slurm" and not self.image:
            raise AReaLOPDConfigurationError(
                "areal.opd.image is required when scheduler_type='slurm'."
            )


class AReaLOPDWrapper:
    """Launch an AReaL Experience OPD recipe and resolve its checkpoint."""

    def __init__(self, config: AReaLOPDConfig, *, runner: ProcessRunner | None = None) -> None:
        self.config = config
        self._runner = runner or subprocess.run

    def train(
        self,
        base_ckpt: str,
        dataset: Mapping[str, Any] | None,
        train_config: Mapping[str, Any],
    ) -> TrainRun:
        """Launch OPD for one iteration and return the resulting checkpoint."""

        dataset = dataset or {}
        experience_path = self._require_path(dataset, train_config, "experience_path")
        train_data_path = self._require_path(dataset, train_config, "train_data_path")
        valid_data_path = (
            _first_str(dataset, train_config, "valid_data_path") or train_data_path
        )
        experiment_name = str(train_config.get("experiment_name") or self.config.experiment_name)
        trial_name = str(train_config.get("trial_name") or self.config.trial_name)

        config_path = Path(self.config.config_path)
        if not config_path.is_absolute():
            config_path = self.config.working_directory / config_path

        overrides = [
            f"actor.path={base_ckpt}",
            f"experience_path={experience_path}",
            f"train_dataset.path={train_data_path}",
            f"valid_dataset.path={valid_data_path}",
            f"experiment_name={experiment_name}",
            f"trial_name={trial_name}",
            f"cluster.fileroot={self.config.fileroot}",
        ]
        launch_command = [*self.config.command, "--config", str(config_path), *overrides]

        metadata: dict[str, Any]
        if self.config.scheduler_type == "slurm":
            metadata = self._run_slurm(launch_command, experiment_name, trial_name)
        else:
            metadata = self._run_local(launch_command, experiment_name, trial_name)

        checkpoint = self._latest_checkpoint(experiment_name, trial_name)
        if checkpoint is None:
            raise AReaLOPDTrainingError(
                f"OPD training finished but no checkpoint was found under "
                f"{self._checkpoint_root(experiment_name, trial_name)}. "
                f"See logs: {metadata.get('log_path') or metadata.get('slurm_log_path')}"
            )
        return {
            "train_run_id": f"{experiment_name}:{trial_name}",
            "method": "opd",
            "base_ckpt": base_ckpt,
            "ckpt": str(checkpoint),
            "metrics": {},
            "metadata": {
                "experiment_name": experiment_name,
                "trial_name": trial_name,
                "experience_path": str(experience_path),
                "train_data_path": str(train_data_path),
                **metadata,
            },
        }

    # -- local -------------------------------------------------------------

    def _run_local(self, command: list[str], experiment_name: str, trial_name: str) -> dict[str, Any]:
        log_dir = self.config.fileroot / "rsi-opd-logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{experiment_name}-{trial_name}.log"
        with log_path.open("w", encoding="utf-8") as log_file:
            result = self._runner(
                command,
                cwd=str(self.config.working_directory),
                env=self._environment(),
                text=True,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
        if getattr(result, "returncode", 0) != 0:
            raise AReaLOPDTrainingError(
                f"OPD training exited with code {result.returncode}. See {log_path}.\n"
                f"{_log_tail(log_path)}"
            )
        return {"scheduler_type": "local", "log_path": str(log_path)}

    # -- slurm -------------------------------------------------------------

    def _run_slurm(self, command: list[str], experiment_name: str, trial_name: str) -> dict[str, Any]:
        run_dir = self.config.fileroot / "rsi-opd-slurm" / f"{experiment_name}-{trial_name}"
        run_dir.mkdir(parents=True, exist_ok=True)
        script_path = run_dir / "launch.sbatch"
        script_path.write_text(
            self._render_sbatch(command, experiment_name, trial_name, run_dir),
            encoding="utf-8",
        )
        submit = self._runner(
            [*self.config.sbatch_command, str(script_path)],
            cwd=str(self.config.working_directory),
            env={**os.environ},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        if getattr(submit, "returncode", 0) != 0:
            raise AReaLOPDTrainingError(
                f"sbatch submission failed (code {submit.returncode}): {submit.stdout}"
            )
        job_id = _parse_job_id(submit.stdout)
        if not job_id:
            raise AReaLOPDTrainingError(
                f"sbatch did not return a parsable job id: {submit.stdout!r}"
            )
        self._wait_for_slurm_job(job_id)
        return {
            "scheduler_type": "slurm",
            "job_id": job_id,
            "script_path": str(script_path),
            "slurm_log_path": str(run_dir / f"slurm-{job_id}.out"),
        }

    def _render_sbatch(
        self, command: list[str], experiment_name: str, trial_name: str, run_dir: Path
    ) -> str:
        cfg = self.config
        env = {
            "PYTHONPATH": str(cfg.working_directory),
            "AREAL_ADMIN_API_KEY": secrets.token_hex(16),
            **{str(k): str(v) for k, v in cfg.environment_variables.items()},
        }
        container_args = [*cfg.singularity_command, "--nv", "--no-home", "--writable-tmpfs"]
        for path in cfg.bind_paths:
            container_args += ["--bind", path if ":" in path else f"{path}:{path}"]
        for key, value in env.items():
            container_args += ["--env", f"{key}={value}"]
        inner = f"cd {shlex.quote(str(cfg.working_directory))}\nexec {shlex.join(command)}"
        container_args += [str(cfg.image), "bash", "-lc", inner]
        launch = shlex.join(
            [
                *cfg.srun_command,
                f"--nodes={cfg.n_nodes}",
                f"--ntasks={cfg.n_nodes}",
                "--ntasks-per-node=1",
                f"--cpus-per-task={cfg.cpus_per_task}",
                f"--mem={cfg.memory}",
                f"--gres=gpu:{cfg.n_gpus_per_node}",
                *container_args,
            ]
        )
        directives = [
            f"#SBATCH --job-name=opd-{experiment_name}-{trial_name}",
            f"#SBATCH --nodes={cfg.n_nodes}",
            f"#SBATCH --ntasks={cfg.n_nodes}",
            f"#SBATCH --gres=gpu:{cfg.n_gpus_per_node}",
            f"#SBATCH --cpus-per-task={cfg.cpus_per_task}",
            f"#SBATCH --mem={cfg.memory}",
            f"#SBATCH --time={cfg.time_limit}",
            f"#SBATCH --output={run_dir / 'slurm-%j.out'}",
            f"#SBATCH --error={run_dir / 'slurm-%j.out'}",
        ]
        if cfg.partition:
            directives.append(f"#SBATCH --partition={cfg.partition}")
        if cfg.reservation:
            directives.append(f"#SBATCH --reservation={cfg.reservation}")
        return "\n".join(["#!/usr/bin/env bash", *directives, "", "set -euo pipefail", launch, ""])

    def _wait_for_slurm_job(self, job_id: str) -> None:
        started = time.monotonic()
        seen_running = False
        while True:
            active = self._squeue_active(job_id)
            if active:
                seen_running = True
            elif seen_running or time.monotonic() - started > self.config.poll_interval_seconds:
                # Left the queue: completion detected without sacct. The caller
                # then confirms success by resolving the saved checkpoint.
                return
            if time.monotonic() - started >= self.config.job_timeout_seconds:
                raise AReaLOPDTrainingError(
                    f"OPD Slurm job {job_id} did not finish within "
                    f"{self.config.job_timeout_seconds:g}s."
                )
            time.sleep(max(1.0, self.config.poll_interval_seconds))

    def _squeue_active(self, job_id: str) -> bool:
        result = self._runner(
            ["squeue", "-h", "-j", job_id, "-o", "%T"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if getattr(result, "returncode", 1) != 0:
            return False
        return bool((result.stdout or "").strip())

    # -- shared ------------------------------------------------------------

    def _environment(self) -> dict[str, str]:
        environment = {
            **os.environ,
            "PYTHONPATH": str(self.config.working_directory),
            **{str(k): str(v) for k, v in self.config.environment_variables.items()},
        }
        environment.setdefault("AREAL_ADMIN_API_KEY", secrets.token_hex(16))
        return environment

    def _checkpoint_root(self, experiment_name: str, trial_name: str) -> Path:
        return self.config.fileroot / "checkpoints" / getpass.getuser() / experiment_name / trial_name / "default"

    def _latest_checkpoint(self, experiment_name: str, trial_name: str) -> Path | None:
        # The training job may run as a different user inside the container, so
        # search across any <user> segment, not just the controller's user.
        roots = list(
            (self.config.fileroot / "checkpoints").glob(
                f"*/{experiment_name}/{trial_name}/default"
            )
        )
        candidates = [
            path
            for root in roots
            for path in root.glob("*globalstep*")
            if path.is_dir()
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda path: (_globalstep(path), path.stat().st_mtime))

    @staticmethod
    def _require_path(dataset: Mapping[str, Any], train_config: Mapping[str, Any], key: str) -> str:
        value = _first_str(dataset, train_config, key)
        if not value:
            raise AReaLOPDTrainingError(
                f"OPD training requires '{key}' in the dataset or train_config."
            )
        return value


def _first_str(*sources_and_key: Any) -> str | None:
    *sources, key = sources_and_key
    for source in sources:
        value = source.get(key) if isinstance(source, Mapping) else None
        if isinstance(value, str) and value.strip():
            return value
    return None


def _parse_job_id(stdout: str | None) -> str:
    for line in (stdout or "").splitlines():
        token = line.strip().split(";", 1)[0].split()[0] if line.strip() else ""
        if token.isdigit():
            return token
    return ""


def _globalstep(path: Path) -> int:
    match = re.search(r"globalstep(\d+)", path.name)
    return int(match.group(1)) if match else -1


def _log_tail(log_path: Path, *, lines: int = 20) -> str:
    try:
        content = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
        raise AReaLOPDConfigurationError(f"{field_name} must be a list of strings.")
    return tuple(value)


def _string_mapping(value: Any, field_name: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise AReaLOPDConfigurationError(f"{field_name} must be a string-to-string mapping.")
    return dict(value)
