"""AReaL service entry point with SFT training support."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from areal_rsi.schemas.core import Dataset, Endpoint, ModelRef, TrainMethod, TrainRun

from .mock import MockAReaLRolloutWrapper
from .sft_wrapper import AReaLSFTConfig, AReaLSFTWrapper, ProcessRunner


class AReaLServiceConfigurationError(RuntimeError):
    """Raised when an unconfigured AReaL capability is requested."""


class AReaLService:
    """Hosts configured AReaL SFT training and rollout endpoint capabilities."""

    def __init__(
        self,
        sft_config: AReaLSFTConfig | None = None,
        *,
        rollout_wrapper: MockAReaLRolloutWrapper | None = None,
        runner: ProcessRunner | None = None,
    ) -> None:
        self._sft_wrapper = (
            AReaLSFTWrapper(sft_config, runner=runner) if sft_config is not None else None
        )
        self._rollout_wrapper = rollout_wrapper

    @classmethod
    def from_yaml(cls, path: str | Path, *, runner: ProcessRunner | None = None) -> "AReaLService":
        """Construct the service from a YAML containing ``areal.sft`` and ``areal.rollout``."""

        return cls(
            sft_config=AReaLSFTConfig.from_yaml(path),
            rollout_wrapper=MockAReaLRolloutWrapper.from_yaml(path),
            runner=runner,
        )

    @classmethod
    def from_mock_rollout_config(cls, path: str | Path) -> "AReaLService":
        """Construct a serving-only service backed by configured endpoint responses."""

        return cls(rollout_wrapper=MockAReaLRolloutWrapper.from_yaml(path))

    def ensure_rollout_endpoint(self, ckpt: str, config: dict[str, Any] | None = None) -> Endpoint:
        """Return the configured rollout endpoint."""

        return self._serving_wrapper().ensure_rollout_endpoint(ckpt, config)

    def update_rollout_endpoint(self, endpoint: Endpoint, ckpt: str) -> Endpoint:
        """Return the configured endpoint-update response."""

        return self._serving_wrapper().update_rollout_endpoint(endpoint, ckpt)

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
        """Run a synchronous local SFT experiment selected by ``train_config``.

        The controller-facing arguments are retained for compatibility; the
        complete launcher request is in ``train_config``. Slurm runs are
        asynchronous and cannot provide the checkpoint required by the RSI
        loop, so callers must use ``start_experiment`` for them.
        """

        del base_ckpt, dataset, envarena, model_ref, harness_config
        if method != "sft":
            raise AReaLServiceConfigurationError(
                f"SFT is the only configured training method; received {method!r}."
            )
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

    def _serving_wrapper(self) -> MockAReaLRolloutWrapper:
        if self._rollout_wrapper is None:
            raise AReaLServiceConfigurationError(
                "Rollout serving is not configured. Initialize AReaLService with "
                "a rollout wrapper or service YAML containing areal.rollout."
            )
        return self._rollout_wrapper

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
