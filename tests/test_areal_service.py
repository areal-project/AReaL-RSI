from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from areal_rsi.services.areal import AReaLService, AReaLServiceConfigurationError


class StubSFTWrapper:
    def __init__(self) -> None:
        self.run_calls: list[tuple[str | Path, str | None]] = []
        self.submit_calls: list[tuple[str | Path, str | None]] = []
        self.artifact_calls: list[str | Path] = []

    def run_experiment(
        self, config_path: str | Path, *, driver: str | None = None
    ) -> dict[str, Any]:
        self.run_calls.append((config_path, driver))
        return {"train_run_id": "sft-1", "method": "sft", "ckpt": "/checkpoints/1"}

    def submit_experiment(
        self, config_path: str | Path, *, driver: str | None = None
    ) -> dict[str, Any]:
        self.submit_calls.append((config_path, driver))
        return {"job_id": "123", "status": "submitted"}

    def get_experiment_artifacts(self, config_path: str | Path) -> dict[str, Any]:
        self.artifact_calls.append(config_path)
        return {"latest_checkpoint": "/checkpoints/1"}


def test_areal_service_routes_existing_train_config_to_sft_wrapper(tmp_path: Path):
    wrapper = StubSFTWrapper()
    service = AReaLService(wrapper)  # type: ignore[arg-type]
    experiment_config = tmp_path / "experiment.yaml"
    train_config = {"experiment_config": experiment_config, "driver": "examples.sft:main"}

    train_run = service.train("/models/base", {"examples": []}, "sft", train_config)
    submission = service.submit_experiment(train_config)
    artifacts = service.get_experiment_artifacts(train_config)

    assert train_run["ckpt"] == "/checkpoints/1"
    assert submission["job_id"] == "123"
    assert artifacts["latest_checkpoint"] == "/checkpoints/1"
    assert wrapper.run_calls == [(experiment_config, "examples.sft:main")]
    assert wrapper.submit_calls == [(experiment_config, "examples.sft:main")]
    assert wrapper.artifact_calls == [experiment_config]


def test_areal_service_rejects_unsupported_or_incomplete_train_config():
    service = AReaLService(StubSFTWrapper())  # type: ignore[arg-type]

    with pytest.raises(AReaLServiceConfigurationError, match="only configured training method"):
        service.train("/models/base", {}, "opd", {"experiment_config": "/tmp/opd.yaml"})
    with pytest.raises(AReaLServiceConfigurationError, match="experiment_config"):
        service.train("/models/base", {}, "sft", {"epochs": 1})
