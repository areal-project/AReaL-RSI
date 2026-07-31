from __future__ import annotations

from pathlib import Path

import pytest

from areal_rsi.services.areal import (
    AReaLService,
    AReaLServiceConfigurationError,
    MockAReaLRolloutConfig,
    MockAReaLRolloutConfigurationError,
    MockAReaLRolloutWrapper,
)


def _write_config(path: Path) -> None:
    path.write_text(
        """
areal:
  rollout:
    ensure_rollout_endpoint:
      model_ref: model@initial
      base_url: https://mock.example/initial
      metadata:
        state: ready
    update_rollout_endpoint:
      model_ref: model@updated
      base_url: https://mock.example/updated
      metadata:
        state: updated
  sft:
    commands: [areal, train, run]
""".lstrip(),
        encoding="utf-8",
    )


def test_mock_rollout_wrapper_returns_configured_endpoint_responses(tmp_path: Path):
    config_path = tmp_path / "areal.yaml"
    _write_config(config_path)
    wrapper = MockAReaLRolloutWrapper.from_yaml(config_path)

    ensured = wrapper.ensure_rollout_endpoint("/ignored/checkpoint", {"ignored": True})
    updated = wrapper.update_rollout_endpoint(ensured, "/another/ignored/checkpoint")
    ensured["metadata"]["state"] = "mutated"

    assert ensured["model_ref"] == "model@initial"
    assert updated == {
        "model_ref": "model@updated",
        "base_url": "https://mock.example/updated",
        "metadata": {"state": "updated"},
    }
    assert wrapper.ensure_rollout_endpoint("ignored")["metadata"]["state"] == "ready"


def test_areal_service_loads_rollout_and_sft_config_from_one_yaml(tmp_path: Path):
    config_path = tmp_path / "areal.yaml"
    _write_config(config_path)
    service = AReaLService.from_yaml(config_path)

    endpoint = service.ensure_rollout_endpoint("/models/one")
    updated = service.update_rollout_endpoint(endpoint, "/models/two")

    assert endpoint["model_ref"] == "model@initial"
    assert updated["model_ref"] == "model@updated"
    assert service._sft_wrapper is not None


def test_mock_rollout_config_requires_both_endpoint_responses(tmp_path: Path):
    config_path = tmp_path / "invalid.yaml"
    config_path.write_text("areal:\n  rollout: {}\n", encoding="utf-8")

    with pytest.raises(MockAReaLRolloutConfigurationError, match="ensure_rollout_endpoint"):
        MockAReaLRolloutConfig.from_yaml(config_path)
    with pytest.raises(AReaLServiceConfigurationError, match="Rollout serving is not configured"):
        AReaLService().ensure_rollout_endpoint("/models/one")
