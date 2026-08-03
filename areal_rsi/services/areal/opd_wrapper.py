"""Local launcher for AReaL Experience OPD training, mirroring the SFT wrapper.

Unlike SFT (which renders and submits its own sbatch), the Experience OPD
recipes drive AReaL's own scheduler from a single entrypoint invoked as
``<command> --config <yaml> key=value ...``. For the single-node gsm8k recipe
this runs locally and blocks, which is exactly what the RSI loop needs: a
completed checkpoint to hand to holdout evaluation. Slurm-backed OPD (the 4x8
SWE recipe) is intentionally out of scope here and can be added as a second
scheduler branch later.

The wrapper owns only *how to launch*: command, working directory, config YAML,
environment, and where checkpoints land. The per-iteration inputs -- base
checkpoint (actor.path), experience map, and train/valid data -- arrive through
the ``train_config`` dict so the controller can vary them each iteration.
"""

from __future__ import annotations

import getpass
import os
import re
import secrets
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from areal_rsi.schemas.core import TrainRun

ProcessRunner = Any  # Callable[..., subprocess.CompletedProcess[str]]; kept loose for tests.


class AReaLOPDConfigurationError(ValueError):
    """Raised when the areal.opd service config is missing or malformed."""


class AReaLOPDTrainingError(RuntimeError):
    """Raised when an OPD training launch exits non-zero or yields no checkpoint."""


@dataclass(frozen=True, slots=True)
class AReaLOPDConfig:
    """How to launch an AReaL Experience OPD recipe locally.

    ``command`` is the entrypoint up to (but excluding) ``--config <yaml>``;
    ``config_path`` is the AReaL experience YAML (absolute, or relative to
    ``working_directory``). ``fileroot``/``experiment_name`` must match the
    YAML's ``cluster.fileroot``/``experiment_name`` so saved checkpoints can be
    resolved. Per-iteration overrides are appended as ``key=value`` args.
    """

    working_directory: Path
    command: tuple[str, ...]
    config_path: str
    fileroot: Path
    experiment_name: str
    trial_name: str = "rsi"
    scheduler_type: str = "local"
    environment_variables: Mapping[str, str] = field(default_factory=dict)

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
            "working_directory",
            "command",
            "config_path",
            "fileroot",
            "experiment_name",
            "trial_name",
            "scheduler_type",
            "environment_variables",
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
        return cls(
            working_directory=Path(str(section["working_directory"])).expanduser(),
            command=_string_tuple(section["command"], "areal.opd.command"),
            config_path=str(section["config_path"]),
            fileroot=Path(str(section["fileroot"])).expanduser(),
            experiment_name=str(section["experiment_name"]),
            trial_name=str(section.get("trial_name", "rsi")),
            scheduler_type=str(section.get("scheduler_type", "local")),
            environment_variables=_string_mapping(
                section.get("environment_variables", {}), "areal.opd.environment_variables"
            ),
        )

    def __post_init__(self) -> None:
        if self.scheduler_type != "local":
            raise AReaLOPDConfigurationError(
                "Only scheduler_type='local' is currently supported for areal.opd."
            )
        if not self.command:
            raise AReaLOPDConfigurationError("areal.opd.command must be non-empty.")


class AReaLOPDWrapper:
    """Run an AReaL Experience OPD recipe locally and resolve its checkpoint."""

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
        command = [*self.config.command, "--config", str(config_path), *overrides]

        log_dir = self.config.fileroot / "rsi-opd-logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{experiment_name}-{trial_name}.log"
        environment = self._environment()

        with log_path.open("w", encoding="utf-8") as log_file:
            result = self._runner(
                command,
                cwd=str(self.config.working_directory),
                env=environment,
                text=True,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
        if getattr(result, "returncode", 0) != 0:
            raise AReaLOPDTrainingError(
                f"OPD training exited with code {result.returncode}. "
                f"See {log_path} for details.\n{_log_tail(log_path)}"
            )

        checkpoint = self._latest_checkpoint(experiment_name, trial_name)
        if checkpoint is None:
            raise AReaLOPDTrainingError(
                f"OPD training completed but no checkpoint was found under "
                f"{self._checkpoint_root(experiment_name, trial_name)}."
            )
        return {
            "train_run_id": f"{experiment_name}:{trial_name}",
            "method": "opd",
            "base_ckpt": base_ckpt,
            "ckpt": str(checkpoint),
            "metrics": {},
            "metadata": {
                "scheduler_type": "local",
                "experiment_name": experiment_name,
                "trial_name": trial_name,
                "experience_path": str(experience_path),
                "train_data_path": str(train_data_path),
                "log_path": str(log_path),
            },
        }

    def _environment(self) -> dict[str, str]:
        environment = {**os.environ, **{str(k): str(v) for k, v in self.config.environment_variables.items()}}
        environment.setdefault("AREAL_ADMIN_API_KEY", secrets.token_hex(16))
        return environment

    def _checkpoint_root(self, experiment_name: str, trial_name: str) -> Path:
        return (
            self.config.fileroot
            / "checkpoints"
            / getpass.getuser()
            / experiment_name
            / trial_name
            / "default"
        )

    def _latest_checkpoint(self, experiment_name: str, trial_name: str) -> Path | None:
        root = self._checkpoint_root(experiment_name, trial_name)
        if not root.is_dir():
            return None
        candidates = [path for path in root.glob("*globalstep*") if path.is_dir()]
        if not candidates:
            return None
        return max(candidates, key=lambda path: (_globalstep(path), path.stat().st_mtime))

    @staticmethod
    def _require_path(
        dataset: Mapping[str, Any], train_config: Mapping[str, Any], key: str
    ) -> str:
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
