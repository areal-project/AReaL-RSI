"""Thin launcher for AReaL SFT experiment configurations."""

from __future__ import annotations

import getpass
import os
import re
import shlex
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from areal_rsi.schemas.core import TrainRun


class AReaLSFTConfigurationError(ValueError):
    """Raised when an SFT launch configuration is invalid."""


class AReaLSFTTrainingError(RuntimeError):
    """Raised when a local AReaL SFT command exits unsuccessfully."""

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
    """Raised when Slurm rejects a generated SFT job."""

    def __init__(self, *, returncode: int, script_path: Path, stderr: str):
        self.returncode = returncode
        self.script_path = script_path
        self.stderr = stderr
        detail = f"; Slurm said: {stderr.strip()}" if stderr.strip() else ""
        super().__init__(
            f"Submitting AReaL SFT script {script_path} failed with exit code {returncode}{detail}"
        )


ProcessRunner = Callable[..., subprocess.CompletedProcess[str]]
SchedulerType = Literal["local", "slurm"]


@dataclass(frozen=True, slots=True)
class AReaLSlurmConfig:
    """Site-specific settings for the Slurm execution path.

    The training YAML remains the source of truth for model, data, and AReaL
    resources.  This object only contains cluster/container launch settings.
    """

    image: str | Path
    working_directory: str | Path
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
    bind_paths: tuple[str, ...] = ("/storage",)
    cache_root: str | Path | None = None
    script_directory: str | Path | None = None
    log_directory: str | Path | None = None
    offline_datasets: bool = False
    container_environment: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        image = str(self.image).strip()
        working_directory = str(self.working_directory).strip()
        if not image:
            raise AReaLSFTConfigurationError("Slurm image must be non-empty.")
        if not working_directory:
            raise AReaLSFTConfigurationError("Slurm working_directory must be non-empty.")
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
            _validate_command(command, f"Slurm {name}")
        if not self.bind_paths or any(not value or "\n" in value for value in self.bind_paths):
            raise AReaLSFTConfigurationError("bind_paths must contain non-empty single-line paths.")
        object.__setattr__(self, "image", Path(image))
        object.__setattr__(self, "working_directory", Path(working_directory))
        object.__setattr__(self, "sbatch_command", tuple(self.sbatch_command))
        object.__setattr__(self, "srun_command", tuple(self.srun_command))
        object.__setattr__(self, "singularity_command", tuple(self.singularity_command))
        object.__setattr__(self, "torchrun_command", tuple(self.torchrun_command))
        object.__setattr__(self, "bind_paths", tuple(self.bind_paths))
        object.__setattr__(self, "cache_root", Path(self.cache_root) if self.cache_root else None)
        object.__setattr__(
            self, "script_directory", Path(self.script_directory) if self.script_directory else None
        )
        object.__setattr__(
            self, "log_directory", Path(self.log_directory) if self.log_directory else None
        )
        object.__setattr__(
            self,
            "container_environment",
            _validate_environment(self.container_environment, "slurm.container_environment"),
        )


@dataclass(frozen=True, slots=True)
class AReaLSFTConfig:
    """SFT launcher settings, independent of the AReaL experiment itself.

    The four fields deliberately describe only how to launch an already
    complete training YAML: scheduler choice, environment, Slurm settings, and
    the command used as the AReaL entrypoint.  Driver, model, and dataset
    selection belong in the supplied training YAML or command arguments.
    """

    scheduler_type: SchedulerType = "local"
    environment_variables: Mapping[str, str] = field(default_factory=dict)
    slurm: AReaLSlurmConfig | None = None
    commands: tuple[str, ...] = ("areal", "train", "run")

    @classmethod
    def from_yaml(cls, path: str | Path) -> "AReaLSFTConfig":
        """Load the ``areal.sft`` section from an AReaL service YAML file."""

        config_path = Path(path)
        try:
            payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except OSError as error:
            raise AReaLSFTConfigurationError(
                f"Unable to read AReaL SFT service config: {config_path}"
            ) from error
        except yaml.YAMLError as error:
            raise AReaLSFTConfigurationError(
                f"AReaL SFT service config is not valid YAML: {config_path}"
            ) from error
        if not isinstance(payload, Mapping):
            raise AReaLSFTConfigurationError("SFT service config must contain a top-level YAML mapping.")
        areal = payload.get("areal")
        section = areal.get("sft") if isinstance(areal, Mapping) else None
        if not isinstance(section, Mapping):
            raise AReaLSFTConfigurationError(
                "SFT service config must contain an 'areal.sft' YAML mapping."
            )
        unknown_fields = set(section) - {
            "scheduler_type",
            "environment_variables",
            "slurm",
            "commands",
        }
        if unknown_fields:
            fields = ", ".join(sorted(map(str, unknown_fields)))
            raise AReaLSFTConfigurationError(
                f"Unsupported areal.sft fields: {fields}. "
                "Use scheduler_type, environment_variables, slurm, and commands."
            )
        slurm_value = section.get("slurm")
        return cls(
            scheduler_type=section.get("scheduler_type", "local"),
            environment_variables=_yaml_mapping(
                section.get("environment_variables", {}), "areal.sft.environment_variables"
            ),
            slurm=_slurm_config_from_yaml(slurm_value) if slurm_value is not None else None,
            commands=_yaml_string_tuple(section.get("commands", ["areal", "train", "run"]), "areal.sft.commands"),
        )

    def __post_init__(self) -> None:
        if self.scheduler_type not in {"local", "slurm"}:
            raise AReaLSFTConfigurationError(
                "scheduler_type must be either 'local' or 'slurm'."
            )
        _validate_command(self.commands, "commands")
        if self.slurm is not None and not isinstance(self.slurm, AReaLSlurmConfig):
            raise AReaLSFTConfigurationError("slurm must be an AReaLSlurmConfig instance.")
        object.__setattr__(
            self,
            "environment_variables",
            _validate_environment(self.environment_variables, "environment_variables"),
        )
        object.__setattr__(self, "commands", tuple(self.commands))


@dataclass(frozen=True, slots=True)
class _AReaLExperiment:
    config_path: Path
    experiment_name: str
    trial_name: str
    fileroot: Path
    model_path: str
    n_nodes: int | None
    n_gpus_per_node: int | None


class AReaLSFTWrapper:
    """Start a complete AReaL SFT YAML locally or through Slurm."""

    def __init__(self, config: AReaLSFTConfig, *, runner: ProcessRunner | None = None) -> None:
        self.config = config
        self._runner = runner or subprocess.run

    def start_experiment(
        self,
        train_config_path: str | Path,
        *,
        environment_variables: Mapping[str, str] | None = None,
        command: Sequence[str] | None = None,
        scheduler_type: SchedulerType | None = None,
    ) -> TrainRun | dict[str, Any]:
        """Start ``train_config_path`` using the selected scheduler.

        ``command`` is the complete entrypoint command except for the AReaL
        config argument, which is appended as ``--config <train_config_path>``.
        If a command argument is exactly ``{train_config_path}``, it is
        replaced instead, letting an entrypoint use a positional config path.
        Local runs block and return a completed ``TrainRun``. Slurm runs submit
        asynchronously and return submission metadata.
        """

        experiment = _load_experiment(train_config_path)
        resolved_environment = {
            **self.config.environment_variables,
            **_validate_environment(environment_variables or {}, "environment_variables"),
        }
        resolved_command = tuple(command) if command is not None else self.config.commands
        _validate_command(resolved_command, "command")
        resolved_scheduler = scheduler_type or self.config.scheduler_type
        if resolved_scheduler not in {"local", "slurm"}:
            raise AReaLSFTConfigurationError(
                "scheduler_type must be either 'local' or 'slurm'."
            )
        launch_command = _command_with_config(resolved_command, experiment.config_path)
        if resolved_scheduler == "local":
            return self._start_local(experiment, launch_command, resolved_environment)
        return self._start_slurm(experiment, launch_command, resolved_environment)

    def get_experiment_artifacts(self, train_config_path: str | Path) -> dict[str, Any]:
        """Return the logs and checkpoints declared by an experiment YAML."""

        return self._artifact_record(_load_experiment(train_config_path))

    def _start_local(
        self,
        experiment: _AReaLExperiment,
        command: list[str],
        environment_variables: Mapping[str, str],
    ) -> TrainRun:
        artifact = self._artifact_record(experiment)
        log_dir = Path(artifact["log_dir"])
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "areal-rsi-wrapper.log"
        with log_path.open("w", encoding="utf-8") as log_file:
            result = self._runner(
                command,
                env={**os.environ, **environment_variables},
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
        return {
            "train_run_id": f"{experiment.experiment_name}:{experiment.trial_name}",
            "method": "sft",
            "base_ckpt": experiment.model_path,
            "ckpt": checkpoint,
            "metrics": {},
            "metadata": {
                "status": "completed",
                "scheduler_type": "local",
                "train_config_path": str(experiment.config_path),
                "command": command,
                "log_path": str(log_path),
                "artifacts": artifact,
            },
        }

    def _start_slurm(
        self,
        experiment: _AReaLExperiment,
        command: list[str],
        environment_variables: Mapping[str, str],
    ) -> dict[str, Any]:
        slurm = self._require_slurm()
        script_path, log_directory, _ = self._slurm_paths(experiment)
        script_path.parent.mkdir(parents=True, exist_ok=True)
        log_directory.mkdir(parents=True, exist_ok=True)
        script_path.write_text(
            self._render_sbatch_script(experiment, command, environment_variables), encoding="utf-8"
        )
        script_path.chmod(0o750)
        result = self._runner(
            [*slurm.sbatch_command, str(script_path)],
            env={**os.environ, **environment_variables},
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
        return {
            "job_id": job_id,
            "status": "submitted",
            "train_run_id": f"{experiment.experiment_name}:{experiment.trial_name}",
            "method": "sft",
            "base_ckpt": experiment.model_path,
            "scheduler_type": "slurm",
            "train_config_path": str(experiment.config_path),
            "command": command,
            "script_path": str(script_path),
            "slurm_log_path": str(log_directory / f"slurm-{job_id}.out"),
            "slurm_error_path": str(log_directory / f"slurm-{job_id}.err"),
            "artifacts": self._artifact_record(experiment),
        }

    def _artifact_record(self, experiment: _AReaLExperiment) -> dict[str, Any]:
        username = getpass.getuser()
        checkpoint_root = (
            experiment.fileroot
            / "checkpoints"
            / username
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
            / username
            / experiment.experiment_name
            / experiment.trial_name
        )
        return {
            "experiment_name": experiment.experiment_name,
            "trial_name": experiment.trial_name,
            "train_config_path": str(experiment.config_path),
            "log_dir": str(log_dir),
            "checkpoint_root": str(checkpoint_root),
            "checkpoints": [str(path) for path in checkpoints],
            "latest_checkpoint": str(checkpoints[-1]) if checkpoints else None,
        }

    def _require_slurm(self) -> AReaLSlurmConfig:
        if self.config.slurm is None:
            raise AReaLSFTConfigurationError(
                "Slurm launch requires AReaLSFTConfig(slurm=AReaLSlurmConfig(...))."
            )
        if not self.config.slurm.working_directory.is_dir():
            raise AReaLSFTConfigurationError(
                "Slurm working_directory does not exist: "
                f"{self.config.slurm.working_directory}"
            )
        return self.config.slurm

    def _render_sbatch_script(
        self,
        experiment: _AReaLExperiment,
        command: Sequence[str],
        environment_variables: Mapping[str, str],
    ) -> str:
        slurm = self._require_slurm()
        if experiment.n_nodes is None or experiment.n_gpus_per_node is None:
            raise AReaLSFTConfigurationError(
                "Slurm launch requires cluster.n_nodes and cluster.n_gpus_per_node "
                "in the experiment YAML."
            )
        _, log_directory, cache_root = self._slurm_paths(experiment)
        working_directory = slurm.working_directory.resolve()
        distributed_command = " ".join(
            (
                shlex.join(slurm.torchrun_command),
                f"--nnodes={experiment.n_nodes}",
                f"--nproc-per-node={experiment.n_gpus_per_node}",
                '--node_rank="${SLURM_NODEID}"',
                '--master_addr="${MASTER_ADDR}"',
                '--master_port="${MASTER_PORT}"',
                shlex.join(command),
            )
        )
        container_command = "\n".join(
            (
                "set -euo pipefail",
                f"cd {shlex.quote(str(working_directory))}",
                f"exec {distributed_command}",
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
            **environment_variables,
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
                f"--nodes={experiment.n_nodes}",
                f"--ntasks={experiment.n_nodes}",
                "--ntasks-per-node=1",
                f"--cpus-per-task={slurm.cpus_per_task}",
                f"--mem={slurm.memory}",
                *container_args,
            ]
        )
        directives = [
            f"#SBATCH --job-name={_slurm_job_name(experiment.experiment_name, experiment.trial_name)}",
            f"#SBATCH --nodes={experiment.n_nodes}",
            f"#SBATCH --ntasks={experiment.n_nodes}",
            f"#SBATCH --gres=gpu:{experiment.n_gpus_per_node}",
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


def _load_experiment(train_config_path: str | Path) -> _AReaLExperiment:
    path = Path(train_config_path)
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
    )


def _command_with_config(command: Sequence[str], train_config_path: Path) -> list[str]:
    config_path = str(train_config_path)
    if "{train_config_path}" in command:
        return [config_path if item == "{train_config_path}" else item for item in command]
    return [*command, "--config", config_path]


def _slurm_config_from_yaml(value: Any) -> AReaLSlurmConfig:
    options = _yaml_mapping(value, "areal.sft.slurm")
    for field_name in (
        "sbatch_command",
        "srun_command",
        "singularity_command",
        "torchrun_command",
        "bind_paths",
    ):
        if field_name in options:
            options[field_name] = _yaml_string_tuple(
                options[field_name], f"areal.sft.slurm.{field_name}"
            )
    if "container_environment" in options:
        options["container_environment"] = _yaml_mapping(
            options["container_environment"], "areal.sft.slurm.container_environment"
        )
    try:
        return AReaLSlurmConfig(**options)
    except TypeError as error:
        raise AReaLSFTConfigurationError(
            "Invalid fields in areal.sft.slurm service configuration."
        ) from error


def _validate_command(value: Sequence[str], field_name: str) -> None:
    if isinstance(value, (str, bytes)) or not value or any(
        not isinstance(part, str) or not part.strip() for part in value
    ):
        raise AReaLSFTConfigurationError(
            f"{field_name} must contain at least one non-empty string argument."
        )


def _validate_environment(value: Mapping[str, str], field_name: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise AReaLSFTConfigurationError(f"{field_name} must be a mapping of environment variables.")
    environment = dict(value)
    for key, item in environment.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise AReaLSFTConfigurationError(
                f"{field_name} keys must be valid shell environment names."
            )
        if not isinstance(item, str) or "\n" in item:
            raise AReaLSFTConfigurationError(
                f"{field_name} values must be single-line strings."
            )
    return environment


def _yaml_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AReaLSFTConfigurationError(f"{field_name} must be a YAML mapping.")
    return dict(value)


def _yaml_string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise AReaLSFTConfigurationError(f"{field_name} must be a YAML list of strings.")
    return tuple(value)


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


def _parse_sbatch_job_id(stdout: str) -> str:
    job_id = stdout.strip().split(";", maxsplit=1)[0]
    if not re.fullmatch(r"\d+(?:_\[?[\d,-]+\]?)?", job_id):
        raise AReaLSFTConfigurationError(
            f"Could not parse Slurm job id from sbatch output: {stdout!r}"
        )
    return job_id


def _slurm_job_name(experiment_name: str, trial_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "-", f"{experiment_name}-{trial_name}")[:128]
