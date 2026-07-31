"""Deterministic AReaL fake used by the local controller tests and demo CLI."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from areal_rsi.schemas.core import Dataset, Endpoint, ModelRef, TrainMethod, TrainRun


class MockAReaLRolloutConfigurationError(ValueError):
    """Raised when a static rollout YAML does not define endpoint responses."""


@dataclass(frozen=True, slots=True)
class MockAReaLRolloutConfig:
    """Configured endpoint responses for the rollout part of ``AReaLService``."""

    ensure_rollout_endpoint: Mapping[str, Any]
    update_rollout_endpoint: Mapping[str, Any]

    @classmethod
    def from_yaml(cls, path: str | Path) -> "MockAReaLRolloutConfig":
        """Load the ``areal.rollout`` section of an AReaL service YAML file."""

        config_path = Path(path)
        try:
            value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except OSError as error:
            raise MockAReaLRolloutConfigurationError(
                f"Unable to read rollout config: {config_path}"
            ) from error
        except yaml.YAMLError as error:
            raise MockAReaLRolloutConfigurationError(
                f"Invalid YAML in rollout config: {config_path}"
            ) from error
        if not isinstance(value, Mapping):
            raise MockAReaLRolloutConfigurationError("Rollout config must be a YAML mapping.")
        areal = value.get("areal")
        rollout = areal.get("rollout") if isinstance(areal, Mapping) else None
        if not isinstance(rollout, Mapping):
            raise MockAReaLRolloutConfigurationError(
                "AReaL service config must contain an 'areal.rollout' mapping."
            )
        return cls(
            ensure_rollout_endpoint=_endpoint_response(rollout, "ensure_rollout_endpoint"),
            update_rollout_endpoint=_endpoint_response(rollout, "update_rollout_endpoint"),
        )


class MockAReaLRolloutWrapper:
    """Return rollout endpoint responses supplied by ``MockAReaLRolloutConfig``."""

    def __init__(self, config: MockAReaLRolloutConfig) -> None:
        self.config = config

    @classmethod
    def from_yaml(cls, path: str | Path) -> "MockAReaLRolloutWrapper":
        """Construct the rollout wrapper from an AReaL service YAML."""

        return cls(MockAReaLRolloutConfig.from_yaml(path))

    def ensure_rollout_endpoint(
        self, ckpt: str, config: dict[str, Any] | None = None
    ) -> Endpoint:
        """Return the configured response for rollout endpoint startup."""

        del ckpt, config
        return copy.deepcopy(dict(self.config.ensure_rollout_endpoint))

    def update_rollout_endpoint(self, endpoint: Endpoint, ckpt: str) -> Endpoint:
        """Return the configured response for rollout endpoint replacement."""

        del endpoint, ckpt
        return copy.deepcopy(dict(self.config.update_rollout_endpoint))


class MockAReaLService:
    """In-memory fake for the controller's AReaL contract."""

    def ensure_rollout_endpoint(self, ckpt: str, config: dict[str, Any] | None = None) -> Endpoint:
        return {
            "model_ref": ckpt,
            "base_url": f"mock://areal/{ckpt}",
            "api_key": "mock-api-key",
            "protocol": "openai-compatible",
            "metadata": {"config": config or {}},
        }

    def update_rollout_endpoint(self, endpoint: Endpoint, ckpt: str) -> Endpoint:
        updated = dict(endpoint)
        updated["model_ref"] = ckpt
        updated["base_url"] = f"mock://areal/{ckpt}"
        updated["metadata"] = {**updated.get("metadata", {}), "updated_to": ckpt}
        return updated

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
        del envarena
        examples = dataset.get("examples", [])
        next_ckpt = f"{base_ckpt}-{method}-mock"
        return {
            "train_run_id": f"train_{method}_{base_ckpt}",
            "method": method,
            "base_ckpt": base_ckpt,
            "ckpt": next_ckpt,
            "metrics": {
                "num_examples": len(examples),
                "mock_loss": max(0.01, 1.0 / max(1, len(examples))),
            },
            "metadata": {
                "config": config,
                "model_ref": model_ref,
                "harness_config": harness_config or {},
            },
        }


def _endpoint_response(rollout: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = rollout.get(name)
    if not isinstance(value, Mapping):
        raise MockAReaLRolloutConfigurationError(
            f"Rollout config field 'areal.rollout.{name}' must be a mapping."
        )
    return dict(value)
