"""Launcher for AReaL Experience OPD training via its Slurm driver script.

Experience OPD is a nested-scheduler recipe: a CPU driver job (e.g.
``examples/mopd/sbatch_experience_opd_qwen3_30b.sh``) runs the training
entrypoint which, through AReaL's own Slurm scheduler, allocates the colocated
actor/rollout/teacher worker nodes. Re-rendering that sbatch here would mean
reimplementing the driver (munge/slurm binds, dependency install, allocation
mode, ...), so this wrapper instead *reuses* the checked-in driver script:

  set the driver's environment contract -> ``sbatch --parsable <driver>`` ->
  wait (squeue only, no sacct) until the driver job leaves the queue ->
  resolve the saved checkpoint.

The environment-variable interface of the driver script is the integration
contract. Per-iteration inputs (current checkpoint, the Researcher's experience
map, the benchmark train data) are passed as MODEL_PATH / EXPERIENCE_PATH /
TRAIN_DATA; deployment paths come from ``environment_variables``; secrets
(AENV_SYSTEM_URL, WANDB_*, PYPI_INDEX_URL, ...) are inherited from the process
environment at runtime and never stored in config.
"""

from __future__ import annotations

import getpass
import os
import re
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from areal_rsi.schemas.core import TrainRun

ProcessRunner = Any  # Callable[..., subprocess.CompletedProcess[str]]; kept loose for tests.

_ACTIVE_SLURM_STATES = {
    "RUNNING", "PENDING", "COMPLETING", "CONFIGURING", "RESIZING", "REQUEUED", "SUSPENDED",
}


class AReaLOPDConfigurationError(ValueError):
    """Raised when the areal.opd service config is missing or malformed."""


class AReaLOPDTrainingError(RuntimeError):
    """Raised when an OPD launch fails to submit or yields no checkpoint."""


@dataclass(frozen=True, slots=True)
class AReaLOPDConfig:
    """How to launch an AReaL Experience OPD driver on Slurm.

    ``driver_script`` is the checked-in AReaL sbatch driver (absolute, or
    relative to ``working_directory``). ``fileroot``/``experiment_name`` must
    match the recipe so the saved checkpoint can be resolved.
    """

    working_directory: Path
    driver_script: str
    fileroot: Path
    experiment_name: str
    trial_name: str = "rsi"
    scheduler_type: str = "slurm"
    n_nodes: int = 4
    n_gpus_per_node: int = 8
    environment_variables: Mapping[str, str] = field(default_factory=dict)
    sbatch_command: tuple[str, ...] = ("sbatch", "--parsable")
    poll_interval_seconds: float = 60.0
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
            "working_directory", "driver_script", "fileroot", "experiment_name",
            "trial_name", "scheduler_type", "n_nodes", "n_gpus_per_node",
            "environment_variables", "sbatch_command", "poll_interval_seconds",
            "job_timeout_seconds",
        }
        unknown = set(section) - allowed
        if unknown:
            raise AReaLOPDConfigurationError(
                f"Unsupported areal.opd fields: {', '.join(sorted(map(str, unknown)))}."
            )
        for required in ("working_directory", "driver_script", "fileroot", "experiment_name"):
            if not section.get(required):
                raise AReaLOPDConfigurationError(
                    f"areal.opd.{required} is required and must be non-empty."
                )
        kwargs: dict[str, Any] = dict(
            working_directory=Path(str(section["working_directory"])).expanduser(),
            driver_script=str(section["driver_script"]),
            fileroot=Path(str(section["fileroot"])).expanduser(),
            experiment_name=str(section["experiment_name"]),
            trial_name=str(section.get("trial_name", "rsi")),
            scheduler_type=str(section.get("scheduler_type", "slurm")),
            environment_variables=_string_mapping(
                section.get("environment_variables", {}), "areal.opd.environment_variables"
            ),
        )
        for key in ("n_nodes", "n_gpus_per_node"):
            if section.get(key) is not None:
                kwargs[key] = int(section[key])
        for key in ("poll_interval_seconds", "job_timeout_seconds"):
            if section.get(key) is not None:
                kwargs[key] = float(section[key])
        if section.get("sbatch_command") is not None:
            kwargs["sbatch_command"] = _string_tuple(section["sbatch_command"], "areal.opd.sbatch_command")
        return cls(**kwargs)

    def __post_init__(self) -> None:
        if self.scheduler_type != "slurm":
            raise AReaLOPDConfigurationError(
                "areal.opd.scheduler_type must be 'slurm' (Experience OPD is nested-Slurm only)."
            )
        if not self.driver_script:
            raise AReaLOPDConfigurationError("areal.opd.driver_script must be non-empty.")


class AReaLOPDWrapper:
    """Submit the AReaL Experience OPD Slurm driver and resolve its checkpoint."""

    def __init__(self, config: AReaLOPDConfig, *, runner: ProcessRunner | None = None) -> None:
        self.config = config
        self._runner = runner or subprocess.run

    def train(
        self,
        base_ckpt: str,
        dataset: Mapping[str, Any] | None,
        train_config: Mapping[str, Any],
    ) -> TrainRun:
        """Launch the OPD driver for one iteration and return its checkpoint."""

        dataset = dataset or {}
        experience_path = self._require_path(dataset, train_config, "experience_path")
        train_data_path = self._require_path(dataset, train_config, "train_data_path")
        valid_data_path = _first_str(dataset, train_config, "valid_data_path")
        experiment_name = str(train_config.get("experiment_name") or self.config.experiment_name)
        trial_name = str(train_config.get("trial_name") or self.config.trial_name)

        driver_script = Path(self.config.driver_script)
        if not driver_script.is_absolute():
            driver_script = self.config.working_directory / driver_script
        if not driver_script.is_file():
            raise AReaLOPDTrainingError(f"OPD driver script not found: {driver_script}")

        run_dir = self.config.fileroot / "rsi-opd-slurm" / f"{experiment_name}-{trial_name}"
        run_dir.mkdir(parents=True, exist_ok=True)

        # Environment contract expected by the AReaL driver script. Deployment
        # paths come from config; secrets (AENV_SYSTEM_URL, WANDB_*, ...) are
        # inherited from the process environment; the loop supplies MODEL_PATH,
        # EXPERIENCE_PATH and TRAIN_DATA.
        env = {**os.environ, **{str(k): str(v) for k, v in self.config.environment_variables.items()}}
        env.update(
            {
                "AREAL_DIR": str(self.config.working_directory),
                "MODEL_PATH": base_ckpt,
                "TOKENIZER_PATH": base_ckpt,
                "TRAIN_DATA": train_data_path,
                "EXPERIENCE_PATH": experience_path,
                "FILEROOT": str(self.config.fileroot),
                "EXP_NAME": experiment_name,
                "TRIAL_NAME": trial_name,
                "N_NODES": str(self.config.n_nodes),
                "N_GPUS": str(self.config.n_gpus_per_node),
            }
        )
        if valid_data_path:
            env["VALID_DATA"] = valid_data_path

        submit = self._runner(
            [*self.config.sbatch_command, str(driver_script)],
            cwd=str(run_dir),
            env=env,
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

        checkpoint = self._latest_checkpoint(experiment_name, trial_name)
        if checkpoint is None:
            raise AReaLOPDTrainingError(
                f"OPD driver job {job_id} finished but no checkpoint was found under "
                f"{self._checkpoint_root(experiment_name, trial_name)}. See {run_dir}."
            )
        return {
            "train_run_id": f"{experiment_name}:{trial_name}",
            "method": "opd",
            "base_ckpt": base_ckpt,
            "ckpt": str(checkpoint),
            "metrics": {},
            "metadata": {
                "scheduler_type": "slurm",
                "job_id": job_id,
                "experiment_name": experiment_name,
                "trial_name": trial_name,
                "experience_path": str(experience_path),
                "train_data_path": str(train_data_path),
                "driver_script": str(driver_script),
                "run_dir": str(run_dir),
            },
        }

    def _wait_for_slurm_job(self, job_id: str) -> None:
        started = time.monotonic()
        seen_active = False
        while True:
            active = self._squeue_active(job_id)
            if active:
                seen_active = True
            elif seen_active or time.monotonic() - started > self.config.poll_interval_seconds:
                # Driver job left the queue: training finished. Success is
                # confirmed by resolving the checkpoint (no sacct required).
                return
            if time.monotonic() - started >= self.config.job_timeout_seconds:
                raise AReaLOPDTrainingError(
                    f"OPD driver job {job_id} did not finish within "
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

    def _checkpoint_root(self, experiment_name: str, trial_name: str) -> Path:
        return self.config.fileroot / "checkpoints" / getpass.getuser() / experiment_name / trial_name / "default"

    def _latest_checkpoint(self, experiment_name: str, trial_name: str) -> Path | None:
        # The workers may run as a different user inside the container, so search
        # across any <user> segment, not just the controller's user.
        candidates = [
            path
            for root in (self.config.fileroot / "checkpoints").glob(
                f"*/{experiment_name}/{trial_name}/default"
            )
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
