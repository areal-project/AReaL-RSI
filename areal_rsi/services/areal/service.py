"""AReaL service entry point with SFT and Experience OPD training support."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from areal_rsi.schemas.core import Dataset, ModelRef, TrainMethod, TrainRun

from .opd_wrapper import AReaLOPDConfig, AReaLOPDWrapper
from .sft_wrapper import AReaLSFTConfig, AReaLSFTWrapper, ProcessRunner


class AReaLServiceConfigurationError(RuntimeError):
    """Raised when an unconfigured AReaL capability is requested."""


class AReaLService:
    """Hosts the configured AReaL SFT and/or Experience OPD training capabilities."""

    def __init__(
        self,
        sft_config: AReaLSFTConfig | None = None,
        *,
        opd_config: AReaLOPDConfig | None = None,
        runner: ProcessRunner | None = None,
    ) -> None:
        self._sft_wrapper = (
            AReaLSFTWrapper(sft_config, runner=runner) if sft_config is not None else None
        )
        self._opd_wrapper = (
            AReaLOPDWrapper(opd_config, runner=runner) if opd_config is not None else None
        )

    @classmethod
    def from_yaml(cls, path: str | Path, *, runner: ProcessRunner | None = None) -> "AReaLService":
        """Construct the service from a YAML with an ``areal.sft`` and/or ``areal.opd`` section."""

        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        areal = payload.get("areal") if isinstance(payload, Mapping) else None
        has_sft = isinstance(areal, Mapping) and "sft" in areal
        has_opd = isinstance(areal, Mapping) and "opd" in areal
        if not has_sft and not has_opd:
            raise AReaLServiceConfigurationError(
                "AReaL service config must contain an 'areal.sft' or 'areal.opd' section."
            )
        return cls(
            sft_config=AReaLSFTConfig.from_yaml(path) if has_sft else None,
            opd_config=AReaLOPDConfig.from_yaml(path) if has_opd else None,
            runner=runner,
        )

    def train(
        self,
        base_ckpt: str,
        dataset: Dataset,
        method: TrainMethod,
        config: dict[str, Any],
        *,
        envarena: object | None = None,
        model_ref: ModelRef | None = None,
        harness_config: dict[str, Any] | None = None,
    ) -> TrainRun:
        """Run a synchronous local training experiment for ``method``.

        ``opd`` launches AReaL Experience OPD locally through ``AReaLOPDWrapper``,
        blocking until a checkpoint exists. ``sft`` runs a local SFT experiment
        selected by ``config``; Slurm SFT runs are asynchronous and must instead
        use ``start_experiment``.
        """

        if method == "opd":
            return self._opd_training_wrapper().train(base_ckpt, dataset, config)
        del envarena, model_ref, harness_config
        if method != "sft":
            raise AReaLServiceConfigurationError(
                f"Unsupported training method {method!r}; expected 'sft' or 'opd'."
            )
        del base_ckpt, dataset
        _, _, _, scheduler_type = self._launch_options(config)
        if scheduler_type == "slurm" or (
            scheduler_type is None and self._training_wrapper().config.scheduler_type == "slurm"
        ):
            raise AReaLServiceConfigurationError(
                "AReaLService.train() requires scheduler_type='local' because the RSI loop "
                "needs a completed checkpoint. Use start_experiment() for a Slurm submission."
            )
        return self.start_experiment(config)

    def start_experiment(self, train_config: dict[str, Any]) -> TrainRun | dict[str, Any]:
        """Start an SFT experiment using the launcher options in ``train_config``."""

        train_config_path, environment_variables, command, scheduler_type = self._launch_options(
            train_config
        )
        return self._training_wrapper().start_experiment(
            train_config_path,
            environment_variables=environment_variables,
            command=command,
            scheduler_type=scheduler_type,
        )

    def get_experiment_artifacts(self, train_config: dict[str, Any]) -> dict[str, Any]:
        """Return log and checkpoint paths for the configured SFT experiment."""

        train_config_path, _, _, _ = self._launch_options(train_config)
        return self._training_wrapper().get_experiment_artifacts(train_config_path)

    def _training_wrapper(self) -> AReaLSFTWrapper:
        if self._sft_wrapper is None:
            raise AReaLServiceConfigurationError(
                "SFT is not configured. Initialize AReaLService with AReaLSFTConfig."
            )
        return self._sft_wrapper

    def _opd_training_wrapper(self) -> AReaLOPDWrapper:
        if self._opd_wrapper is None:
            raise AReaLServiceConfigurationError(
                "OPD is not configured. Initialize AReaLService with AReaLOPDConfig "
                "(an 'areal.opd' section)."
            )
        return self._opd_wrapper

    @staticmethod
    def _launch_options(
        train_config: dict[str, Any],
    ) -> tuple[str | Path, dict[str, str] | None, tuple[str, ...] | None, str | None]:
        train_config_path = train_config.get(
            "train_config_path", train_config.get("experiment_config")
        )
        if not isinstance(train_config_path, (str, Path)) or not str(train_config_path).strip():
            raise AReaLServiceConfigurationError(
                "train_config must contain a non-empty 'train_config_path' YAML path."
            )
        environment_variables = train_config.get("environment_variables")
        if environment_variables is not None:
            if not isinstance(environment_variables, dict) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in environment_variables.items()
            ):
                raise AReaLServiceConfigurationError(
                    "train_config['environment_variables'] must be a string mapping when set."
                )
        command = train_config.get("command")
        if command is not None:
            if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
                raise AReaLServiceConfigurationError(
                    "train_config['command'] must be a list of strings when set."
                )
            command = tuple(command)
        scheduler_type = train_config.get("scheduler_type")
        if scheduler_type is not None and scheduler_type not in {"local", "slurm"}:
            raise AReaLServiceConfigurationError(
                "train_config['scheduler_type'] must be 'local' or 'slurm' when set."
            )
        return train_config_path, environment_variables, command, scheduler_type
