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
    """Hosts configured AReaL SFT training and mock rollout capabilities."""

    def __init__(
        self,
        sft_wrapper: AReaLSFTWrapper | None = None,
        *,
        rollout_wrapper: MockAReaLRolloutWrapper | None = None,
    ) -> None:
        self._sft_wrapper = sft_wrapper
        self._rollout_wrapper = rollout_wrapper

    @classmethod
    def from_sft_config(
        cls,
        config: AReaLSFTConfig,
        *,
        runner: ProcessRunner | None = None,
    ) -> "AReaLService":
        """Construct the service with the concrete SFT execution settings."""

        return cls(AReaLSFTWrapper(config, runner=runner))

    @classmethod
    def from_mock_rollout_config(cls, path: str | Path) -> "AReaLService":
        """Construct a serving-only service backed by configured mock responses."""

        return cls(rollout_wrapper=MockAReaLRolloutWrapper.from_yaml(path))

    def ensure_rollout_endpoint(self, ckpt: str, config: dict[str, Any] | None = None) -> Endpoint:
        """Return the configured mock rollout endpoint."""

        return self._serving_wrapper().ensure_rollout_endpoint(ckpt, config)

    def update_rollout_endpoint(self, endpoint: Endpoint, ckpt: str) -> Endpoint:
        """Return the configured mock endpoint-update response."""

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
        """Execute an SFT experiment selected by the existing ``train_config``.

        ``config["experiment_config"]`` is the complete AReaL YAML. An
        optional ``config["driver"]`` selects a non-default AReaL driver.
        Model and dataset values remain owned by the YAML; the other original
        train arguments are retained for controller compatibility.
        """

        del base_ckpt, dataset, envarena, model_ref, harness_config
        if method != "sft":
            raise AReaLServiceConfigurationError(
                f"SFT is the only configured training method; received {method!r}."
            )
        experiment_config, driver = self._experiment_options(config)
        return self._training_wrapper().run_experiment(experiment_config, driver=driver)

    def submit_experiment(self, config: dict[str, Any]) -> dict[str, Any]:
        """Submit the SFT experiment identified by ``train_config`` to Slurm."""

        experiment_config, driver = self._experiment_options(config)
        return self._training_wrapper().submit_experiment(experiment_config, driver=driver)

    def get_experiment_artifacts(self, config: dict[str, Any]) -> dict[str, Any]:
        """Return log and checkpoint paths for the configured SFT experiment."""

        experiment_config, _ = self._experiment_options(config)
        return self._training_wrapper().get_experiment_artifacts(experiment_config)

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
                "a MockAReaLRolloutWrapper or mock rollout config."
            )
        return self._rollout_wrapper

    @staticmethod
    def _experiment_options(config: dict[str, Any]) -> tuple[str | Path, str | None]:
        experiment_config = config.get("experiment_config")
        if not isinstance(experiment_config, (str, Path)) or not str(experiment_config).strip():
            raise AReaLServiceConfigurationError(
                "train_config must contain a non-empty 'experiment_config' YAML path."
            )
        driver = config.get("driver")
        if driver is not None and not isinstance(driver, str):
            raise AReaLServiceConfigurationError("train_config['driver'] must be a string when set.")
        return experiment_config, driver
