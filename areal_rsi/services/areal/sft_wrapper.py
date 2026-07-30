"""Configuration-file-first adapter for AReaL supervised fine-tuning."""

from __future__ import annotations

import getpass
import os
import re
import shlex
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from areal_rsi.schemas.core import TrainRun


class AReaLSFTConfigurationError(ValueError):
    """Raised when an AReaL experiment YAML cannot be launched."""


class AReaLSFTTrainingError(RuntimeError):
    """Raised when the AReaL training command exits unsuccessfully."""

    def __init__(self, *, experiment_name: str, trial_name: str, returncode: int, log_path: Path):
        self.experiment_name = experiment_name
        self.trial_name = trial_name
        self.returncode = returncode
        self.log_path = log_path
        super().__init__(
            f"AReaL SFT run {experiment_name!r}/{trial_name!r} failed with exit code "
            f"{returncode}; inspect {log_path}."
        )


class AReaLSFTSubmissionError(RuntimeError):
    """Raised when Slurm rejects a generated AReaL SFT job."""

    def __init__(self, *, returncode: int, script_path: Path, stderr: str):
        self.returncode = returncode
        self.script_path = script_path
        self.stderr = stderr
        detail = f"; Slurm said: {stderr.strip()}" if stderr.strip() else ""
        super().__init__(
            f"Submitting AReaL SFT script {script_path} failed with exit code {returncode}{detail}"
        )


ProcessRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class AReaLSlurmConfig:
    """Cluster settings for the optional containerized SPMD launch path.

    Model, dataset, parallelism, and AReaL artifact configuration belong in the
    experiment YAML. This object deliberately contains only site-specific
    container and Slurm execution settings.
    """

    image: str | Path
    cpus_per_task: int = 64
    memory: str = "400G"
    time_limit: str = "04:00:00"
    partition: str | None = None
    account: str | None = None
    reservation: str | None = None
    sbatch_command: tuple[str, ...] = ("sbatch", "--parsable")
    srun_command: tuple[str, ...] = ("srun", "--mpi=none")
    singularity_command: tuple[str, ...] = ("singularity", "exec")
    torchrun_command: tuple[str, ...] = ("torchrun",)
    entrypoint: str | Path | None = None
    bind_paths: tuple[str, ...] = ("/storage",)
    cache_root: str | Path | None = None
    script_directory: str | Path | None = None
    log_directory: str | Path | None = None
    offline_datasets: bool = False
    container_environment: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        image_value = str(self.image).strip()
        if not image_value:
            raise AReaLSFTConfigurationError("Slurm image must be non-empty.")
        if self.cpus_per_task < 1:
            raise AReaLSFTConfigurationError("Slurm cpus_per_task must be at least 1.")
        for name, value in (("memory", self.memory), ("time_limit", self.time_limit)):
            if not value or "\n" in value:
                raise AReaLSFTConfigurationError(f"Slurm {name} must be a non-empty single line.")
        for name, value in (
            ("partition", self.partition),
            ("account", self.account),
            ("reservation", self.reservation),
        ):
            if value is not None and (not value or "\n" in value):
                raise AReaLSFTConfigurationError(
                    f"Slurm {name} must be a non-empty single line when configured."
                )
        for name, command in (
            ("sbatch_command", self.sbatch_command),
            ("srun_command", self.srun_command),
            ("singularity_command", self.singularity_command),
            ("torchrun_command", self.torchrun_command),
        ):
            if not command or any(not part.strip() for part in command):
                raise AReaLSFTConfigurationError(
                    f"Slurm {name} must contain at least one non-empty argument."
                )
        if not self.bind_paths or any(not value or "\n" in value for value in self.bind_paths):
            raise AReaLSFTConfigurationError("bind_paths must contain non-empty single-line paths.")
        environment = dict(self.container_environment)
        for key, value in environment.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                raise AReaLSFTConfigurationError(
                    "container_environment keys must be valid shell environment names."
                )
            if re.search(r"(?:token|secret|password|api[_-]?key)", key, flags=re.IGNORECASE):
                raise AReaLSFTConfigurationError(
                    "Do not persist credentials in container_environment; inject them at job runtime."
                )
            if "\n" in str(value):
                raise AReaLSFTConfigurationError(
                    "container_environment values must be single-line strings."
                )
        object.__setattr__(self, "image", Path(image_value))
        object.__setattr__(self, "sbatch_command", tuple(self.sbatch_command))
        object.__setattr__(self, "srun_command", tuple(self.srun_command))
        object.__setattr__(self, "singularity_command", tuple(self.singularity_command))
        object.__setattr__(self, "torchrun_command", tuple(self.torchrun_command))
        object.__setattr__(self, "entrypoint", str(self.entrypoint) if self.entrypoint else None)
        object.__setattr__(self, "bind_paths", tuple(self.bind_paths))
        object.__setattr__(self, "cache_root", Path(self.cache_root) if self.cache_root else None)
        object.__setattr__(
            self, "script_directory", Path(self.script_directory) if self.script_directory else None
        )
        object.__setattr__(
            self, "log_directory", Path(self.log_directory) if self.log_directory else None
        )
        object.__setattr__(self, "container_environment", environment)


@dataclass(frozen=True, slots=True)
class AReaLSFTConfig:
    """Execution-environment settings for configuration-file-first SFT runs."""

    command: tuple[str, ...] = ("areal",)
    working_directory: str | Path | None = None
    driver: str = "examples.math.gsm8k_sft:main"
    username: str | None = None
    environment: Mapping[str, str] = field(default_factory=dict)
    wrapper_log_name: str = "areal-rsi-wrapper.log"
    slurm: AReaLSlurmConfig | None = None

    def __post_init__(self) -> None:
        working_directory = Path(self.working_directory) if self.working_directory is not None else None
        if not self.command or any(not part.strip() for part in self.command):
            raise AReaLSFTConfigurationError(
                "AReaL command must contain at least one non-empty argument."
            )
        if working_directory is not None and not working_directory.is_dir():
            raise AReaLSFTConfigurationError(
                f"AReaL working directory does not exist: {working_directory}"
            )
        if ":" not in self.driver:
            raise AReaLSFTConfigurationError(
                "AReaL driver must use the documented 'module.path:function' format."
            )
        if not self.wrapper_log_name or Path(self.wrapper_log_name).name != self.wrapper_log_name:
            raise AReaLSFTConfigurationError("wrapper_log_name must be a file name, not a path.")
        if self.slurm is not None and not isinstance(self.slurm, AReaLSlurmConfig):
            raise AReaLSFTConfigurationError("slurm must be an AReaLSlurmConfig instance.")
        environment = dict(self.environment)
        for key, value in environment.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                raise AReaLSFTConfigurationError(
                    "environment keys must be valid shell environment names."
                )
            if re.search(r"(?:token|secret|password|api[_-]?key)", key, flags=re.IGNORECASE):
                raise AReaLSFTConfigurationError(
                    "Do not persist credentials in environment; inject them at process runtime."
                )
            if "\n" in str(value):
                raise AReaLSFTConfigurationError("environment values must be single-line strings.")
        object.__setattr__(self, "working_directory", working_directory)
        object.__setattr__(self, "command", tuple(self.command))
        object.__setattr__(self, "environment", environment)

    @property
    def artifact_username(self) -> str:
        return self.username or getpass.getuser()


@dataclass(frozen=True, slots=True)
class _AReaLExperiment:
    config_path: Path
    experiment_name: str
    trial_name: str
    fileroot: Path
    model_path: str
    n_nodes: int | None
    n_gpus_per_node: int | None
    scheduler_type: str | None


class AReaLSFTWrapper:
    """Launch and inspect self-contained AReaL SFT experiment YAMLs."""

    def __init__(self, config: AReaLSFTConfig, *, runner: ProcessRunner | None = None) -> None:
        self.config = config
        self._runner = runner or subprocess.run

    def run_experiment(
        self, config_path: str | Path, *, driver: str | None = None
    ) -> TrainRun:
        """Run the model, data, and resource settings declared in ``config_path``."""

        experiment = _load_experiment(config_path)
        resolved_driver = self._resolve_driver(driver)
        artifact = self._artifact_record(experiment)
        log_dir = Path(artifact["log_dir"])
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / self.config.wrapper_log_name
        command = self._build_command(experiment.config_path, resolved_driver)
        with log_path.open("w", encoding="utf-8") as log_file:
            result = self._runner(
                command,
                cwd=str(self.config.working_directory) if self.config.working_directory else None,
                env={**os.environ, **self.config.environment},
                text=True,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                check=False,
            )
        if result.returncode != 0:
            raise AReaLSFTTrainingError(
                experiment_name=experiment.experiment_name,
                trial_name=experiment.trial_name,
                returncode=result.returncode,
                log_path=log_path,
            )
        artifact = self._artifact_record(experiment)
        checkpoint = artifact["latest_checkpoint"]
        if checkpoint is None:
            raise AReaLSFTConfigurationError(
                "AReaL SFT completed without a discoverable checkpoint. Configure saver frequency "
                "in the submitted AReaL YAML."
            )
        return self._train_run(experiment, resolved_driver, checkpoint, log_path, artifact)

    def get_experiment_artifacts(self, config_path: str | Path) -> dict[str, Any]:
        """Return log and checkpoint paths for the experiment declared in the YAML."""

        return self._artifact_record(_load_experiment(config_path))

    def render_sbatch_script(self, config_path: str | Path, *, driver: str | None = None) -> str:
        """Render, without writing, a containerized SPMD Slurm launch script."""

        return self._render_sbatch_script(_load_experiment(config_path), self._resolve_driver(driver))

    def write_sbatch_script(self, config_path: str | Path, *, driver: str | None = None) -> Path:
        """Persist an inspectable Slurm script and return its path."""

        experiment = _load_experiment(config_path)
        script_path, log_directory, _ = self._slurm_paths(experiment)
        script_path.parent.mkdir(parents=True, exist_ok=True)
        log_directory.mkdir(parents=True, exist_ok=True)
        script_path.write_text(
            self._render_sbatch_script(experiment, self._resolve_driver(driver)), encoding="utf-8"
        )
        script_path.chmod(0o750)
        return script_path

    def submit_experiment(
        self, config_path: str | Path, *, driver: str | None = None
    ) -> dict[str, Any]:
        """Generate and submit a Slurm job for the declared experiment."""

        experiment = _load_experiment(config_path)
        resolved_driver = self._resolve_driver(driver)
        slurm = self._require_slurm()
        script_path = self.write_sbatch_script(experiment.config_path, driver=resolved_driver)
        result = self._runner(
            [*slurm.sbatch_command, str(script_path)],
            cwd=str(self.config.working_directory) if self.config.working_directory else None,
            env={**os.environ, **self.config.environment},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        if result.returncode != 0:
            raise AReaLSFTSubmissionError(
                returncode=result.returncode, script_path=script_path, stderr=stderr
            )
        job_id = _parse_sbatch_job_id(stdout)
        _, log_directory, _ = self._slurm_paths(experiment)
        return {
            "job_id": job_id,
            "status": "submitted",
            "train_run_id": f"{experiment.experiment_name}:{experiment.trial_name}",
            "method": "sft",
            "base_ckpt": experiment.model_path,
            "experiment_name": experiment.experiment_name,
            "trial_name": experiment.trial_name,
            "config_path": str(experiment.config_path),
            "driver": resolved_driver,
            "script_path": str(script_path),
            "slurm_log_path": str(log_directory / f"slurm-{job_id}.out"),
            "slurm_error_path": str(log_directory / f"slurm-{job_id}.err"),
            "artifacts": self._artifact_record(experiment),
        }

    def _build_command(self, config_path: Path, driver: str) -> list[str]:
        return [
            *self.config.command,
            "train",
            "run",
            "--config",
            str(config_path),
            "--driver",
            driver,
        ]

    def _resolve_driver(self, driver: str | None) -> str:
        resolved_driver = driver or self.config.driver
        if ":" not in resolved_driver:
            raise AReaLSFTConfigurationError(
                "AReaL driver must use the documented 'module.path:function' format."
            )
        return resolved_driver

    def _artifact_record(self, experiment: _AReaLExperiment) -> dict[str, Any]:
        checkpoint_root = (
            experiment.fileroot
            / "checkpoints"
            / self.config.artifact_username
            / experiment.experiment_name
            / experiment.trial_name
            / "default"
        )
        checkpoints = (
            sorted(
                (path for path in checkpoint_root.glob("epoch*globalstep*") if path.is_dir()),
                key=_checkpoint_sort_key,
            )
            if checkpoint_root.is_dir()
            else []
        )
        log_dir = (
            experiment.fileroot
            / "logs"
            / self.config.artifact_username
            / experiment.experiment_name
            / experiment.trial_name
        )
        return {
            "experiment_name": experiment.experiment_name,
            "trial_name": experiment.trial_name,
            "config_path": str(experiment.config_path),
            "log_dir": str(log_dir),
            "checkpoint_root": str(checkpoint_root),
            "checkpoints": [str(path) for path in checkpoints],
            "latest_checkpoint": str(checkpoints[-1]) if checkpoints else None,
        }

    def _train_run(
        self,
        experiment: _AReaLExperiment,
        driver: str,
        checkpoint: str,
        log_path: Path,
        artifact: Mapping[str, Any],
    ) -> TrainRun:
        return {
            "train_run_id": f"{experiment.experiment_name}:{experiment.trial_name}",
            "method": "sft",
            "base_ckpt": experiment.model_path,
            "ckpt": checkpoint,
            "metrics": {},
            "metadata": {
                "status": "completed",
                "config_path": str(experiment.config_path),
                "driver": driver,
                "log_path": str(log_path),
                "artifacts": dict(artifact),
            },
        }

    def _require_slurm(self) -> AReaLSlurmConfig:
        if self.config.slurm is None:
            raise AReaLSFTConfigurationError(
                "Slurm submission requires AReaLSFTConfig(slurm=AReaLSlurmConfig(...))."
            )
        if self.config.working_directory is None:
            raise AReaLSFTConfigurationError(
                "Slurm submission requires config.working_directory for the containerized repo."
            )
        return self.config.slurm

    def _render_sbatch_script(self, experiment: _AReaLExperiment, driver: str) -> str:
        slurm = self._require_slurm()
        if experiment.scheduler_type not in {None, "null"}:
            raise AReaLSFTConfigurationError(
                "Configuration-file Slurm submission requires scheduler.type: null in the experiment YAML."
            )
        n_nodes = experiment.n_nodes
        n_gpus_per_node = experiment.n_gpus_per_node
        if n_nodes is None or n_gpus_per_node is None:
            raise AReaLSFTConfigurationError(
                "Configuration-file Slurm submission requires cluster.n_nodes and "
                "cluster.n_gpus_per_node in the experiment YAML."
            )
        script_path, log_directory, cache_root = self._slurm_paths(experiment)
        del script_path
        working_directory = self.config.working_directory.resolve()
        entrypoint = slurm.entrypoint or _entrypoint_from_driver(driver)
        command = " ".join(
            (
                shlex.join(slurm.torchrun_command),
                f"--nnodes={n_nodes}",
                f"--nproc-per-node={n_gpus_per_node}",
                '--node_rank="${SLURM_NODEID}"',
                '--master_addr="${MASTER_ADDR}"',
                '--master_port="${MASTER_PORT}"',
                shlex.quote(entrypoint),
                "--config",
                shlex.quote(str(experiment.config_path)),
            )
        )
        container_command = "\n".join(
            (
                "set -euo pipefail",
                f"cd {shlex.quote(str(working_directory))}",
                f"exec {command}",
            )
        )
        container_environment = {
            "PYTHONPATH": str(working_directory),
            "AREAL_SPMD_MODE": "1",
            "AREAL_CACHE_DIR": str(cache_root / "areal"),
            "CUDA_DEVICE_MAX_CONNECTIONS": "1",
            "TOKENIZERS_PARALLELISM": "false",
            "TRITON_CACHE_DIR": str(cache_root / "triton"),
            "PYTORCH_KERNEL_CACHE_PATH": str(cache_root / "kernels"),
            "TORCHINDUCTOR_CACHE_DIR": str(cache_root / "inductor"),
            "HF_HOME": str(cache_root / "hf"),
            "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True",
            **slurm.container_environment,
        }
        if slurm.offline_datasets:
            container_environment["HF_DATASETS_OFFLINE"] = "1"
        container_args: list[str] = [*slurm.singularity_command, "--nv", "--no-home", "--writable-tmpfs"]
        for path in slurm.bind_paths:
            container_args.extend(("--bind", path if ":" in path else f"{path}:{path}"))
        for key, value in container_environment.items():
            container_args.extend(("--env", f"{key}={value}"))
        container_args.extend((str(slurm.image), "bash", "-lc", container_command))
        launch_command = shlex.join(
            [
                *slurm.srun_command,
                f"--nodes={n_nodes}",
                f"--ntasks={n_nodes}",
                "--ntasks-per-node=1",
                f"--cpus-per-task={slurm.cpus_per_task}",
                f"--mem={slurm.memory}",
                *container_args,
            ]
        )
        directives = [
            f"#SBATCH --job-name={_slurm_job_name(experiment.experiment_name, experiment.trial_name)}",
            f"#SBATCH --nodes={n_nodes}",
            f"#SBATCH --ntasks={n_nodes}",
            f"#SBATCH --gres=gpu:{n_gpus_per_node}",
            f"#SBATCH --cpus-per-task={slurm.cpus_per_task}",
            f"#SBATCH --mem={slurm.memory}",
            f"#SBATCH --time={slurm.time_limit}",
            f"#SBATCH --output={log_directory / 'slurm-%j.out'}",
            f"#SBATCH --error={log_directory / 'slurm-%j.err'}",
        ]
        if slurm.partition:
            directives.append(f"#SBATCH --partition={slurm.partition}")
        if slurm.account:
            directives.append(f"#SBATCH --account={slurm.account}")
        if slurm.reservation:
            directives.append(f"#SBATCH --reservation={slurm.reservation}")
        return "\n".join(
            (
                "#!/usr/bin/env bash",
                *directives,
                "",
                "set -euo pipefail",
                f"mkdir -p {shlex.quote(str(cache_root))} {shlex.quote(str(experiment.fileroot))}",
                'MASTER_ADDR="$(scontrol show hostnames "${SLURM_JOB_NODELIST}" | head -n 1)"',
                "export MASTER_ADDR",
                "export MASTER_PORT=$((20000 + SLURM_JOB_ID % 40000))",
                launch_command,
                "",
            )
        )

    def _slurm_paths(self, experiment: _AReaLExperiment) -> tuple[Path, Path, Path]:
        slurm = self._require_slurm()
        root = experiment.fileroot.resolve()
        script_directory = (slurm.script_directory or root / "submissions").resolve()
        log_directory = (slurm.log_directory or root / "slurm-logs").resolve()
        cache_root = (slurm.cache_root or root / "cache").resolve()
        script_path = script_directory / experiment.experiment_name / f"{experiment.trial_name}.sbatch"
        return script_path, log_directory, cache_root


def _load_experiment(config_path: str | Path) -> _AReaLExperiment:
    path = Path(config_path)
    if not path.is_file():
        raise AReaLSFTConfigurationError(f"AReaL config file does not exist: {path}")
    config = _load_yaml_mapping(path)
    experiment_name = _required_config_string(config, "experiment_name")
    trial_name = _required_config_string(config, "trial_name")
    fileroot = _required_config_string(config, "cluster.fileroot")
    model_path = _required_config_string(config, "actor.path")
    _validate_artifact_name(experiment_name, "experiment_name")
    _validate_artifact_name(trial_name, "trial_name")
    if "${" in fileroot:
        raise AReaLSFTConfigurationError(
            "cluster.fileroot in an experiment YAML must be a literal path; "
            "AReaL-RSI does not resolve Hydra interpolations."
        )
    return _AReaLExperiment(
        config_path=path.resolve(),
        experiment_name=experiment_name,
        trial_name=trial_name,
        fileroot=Path(fileroot),
        model_path=model_path,
        n_nodes=_optional_config_int(config, "cluster.n_nodes"),
        n_gpus_per_node=_optional_config_int(config, "cluster.n_gpus_per_node"),
        scheduler_type=_optional_config_string(config, "scheduler.type"),
    )


def _load_yaml_mapping(path: Path) -> Mapping[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as config_file:
            value = yaml.safe_load(config_file)
    except yaml.YAMLError as error:
        raise AReaLSFTConfigurationError(f"AReaL config file is not valid YAML: {path}") from error
    if not isinstance(value, Mapping):
        raise AReaLSFTConfigurationError(f"AReaL config file must contain a YAML mapping: {path}")
    return value


def _config_value(config: Mapping[str, Any], dotted_path: str) -> Any:
    value: Any = config
    for component in dotted_path.split("."):
        if not isinstance(value, Mapping) or component not in value:
            return None
        value = value[component]
    return value


def _required_config_string(config: Mapping[str, Any], dotted_path: str) -> str:
    value = _config_value(config, dotted_path)
    text = str(value).strip() if value is not None else ""
    if not text:
        raise AReaLSFTConfigurationError(
            f"Submitted AReaL experiment YAML must define a non-empty {dotted_path!r}."
        )
    return text


def _optional_config_string(config: Mapping[str, Any], dotted_path: str) -> str | None:
    value = _config_value(config, dotted_path)
    return str(value).strip() if value is not None else None


def _optional_config_int(config: Mapping[str, Any], dotted_path: str) -> int | None:
    value = _config_value(config, dotted_path)
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise AReaLSFTConfigurationError(
            f"{dotted_path} in the experiment YAML must be an integer."
        ) from error
    if parsed < 1:
        raise AReaLSFTConfigurationError(
            f"{dotted_path} in the experiment YAML must be at least 1."
        )
    return parsed


def _validate_artifact_name(value: str, field_name: str) -> None:
    if not value or Path(value).name != value or value in {".", ".."}:
        raise AReaLSFTConfigurationError(f"{field_name} must be a non-empty path component.")


def _checkpoint_sort_key(path: Path) -> tuple[int, float, str]:
    match = re.search(r"globalstep(\d+)$", path.name)
    global_step = int(match.group(1)) if match else -1
    return global_step, path.stat().st_mtime, path.name


def _entrypoint_from_driver(driver: str) -> str:
    module, _, function = driver.partition(":")
    if not module or not function:
        raise AReaLSFTConfigurationError(
            "AReaL driver must use the documented 'module.path:function' format."
        )
    return f"{module.replace('.', '/')}.py"


def _parse_sbatch_job_id(stdout: str) -> str:
    job_id = stdout.strip().split(";", maxsplit=1)[0]
    if not re.fullmatch(r"\d+(?:_\[?[\d,-]+\]?)?", job_id):
        raise AReaLSFTConfigurationError(
            f"Could not parse Slurm job id from sbatch output: {stdout!r}"
        )
    return job_id


def _slurm_job_name(experiment_name: str, trial_name: str) -> str:
    value = f"{experiment_name}-{trial_name}"
    return re.sub(r"[^A-Za-z0-9_.-]", "-", value)[:128]
