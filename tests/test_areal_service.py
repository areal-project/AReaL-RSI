from __future__ import annotations

import getpass
import subprocess
from pathlib import Path
from typing import Any

import pytest

from areal_rsi.services.areal import AReaLSFTConfig, AReaLService, AReaLServiceConfigurationError


def test_areal_service_routes_train_config_to_the_single_sft_launch_api(tmp_path: Path):
    seen: list[tuple[list[str], dict[str, Any]]] = []
    train_config_path = tmp_path / "experiment.yaml"
    artifact_root = tmp_path / "artifacts"
    train_config_path.write_text(
        f"""
experiment_name: service-test
trial_name: trial-1
actor:
  path: /models/base
cluster:
  fileroot: {artifact_root}
""".lstrip(),
        encoding="utf-8",
    )

    def runner(command: list[str], **kwargs: Any):
        seen.append((command, kwargs))
        checkpoint = (
            artifact_root
            / "checkpoints"
            / getpass.getuser()
            / "service-test/trial-1/default/epoch0globalstep1"
        )
        checkpoint.mkdir(parents=True)
        return subprocess.CompletedProcess(command, 0)

    service = AReaLService(
        sft_config=AReaLSFTConfig(commands=("areal", "train", "run")),
        runner=runner,
    )
    train_config = {
        "train_config_path": train_config_path,
        "command": ["python", "examples/service_sft.py"],
        "environment_variables": {"WANDB_MODE": "offline"},
    }

    train_run = service.train("/models/base", {"examples": []}, "sft", train_config)
    artifacts = service.get_experiment_artifacts(train_config)

    assert train_run["ckpt"].endswith("globalstep1")
    assert artifacts["latest_checkpoint"] == train_run["ckpt"]
    assert seen[0][0] == [
        "python",
        "examples/service_sft.py",
        "--config",
        str(train_config_path.resolve()),
    ]
    assert seen[0][1]["env"]["WANDB_MODE"] == "offline"


def test_areal_service_rejects_unsupported_async_or_incomplete_train_config():
    service = AReaLService(sft_config=AReaLSFTConfig())

    with pytest.raises(AReaLServiceConfigurationError, match="only configured training method"):
        service.train("/models/base", {}, "opd", {"train_config_path": "/tmp/opd.yaml"})
    with pytest.raises(AReaLServiceConfigurationError, match="train_config_path"):
        service.train("/models/base", {}, "sft", {"epochs": 1})
    with pytest.raises(AReaLServiceConfigurationError, match="requires scheduler_type='local'"):
        service.train(
            "/models/base",
            {},
            "sft",
            {"train_config_path": "/tmp/sft.yaml", "scheduler_type": "slurm"},
        )


def test_areal_service_constructs_sft_wrapper_from_service_yaml(tmp_path: Path):
    service_config = tmp_path / "areal-sft.yaml"
    service_config.write_text(
        """
areal:
  sft:
    scheduler_type: local
    commands: [python, examples.custom_sft.py]
    environment_variables:
      NCCL_DEBUG: WARN
""".lstrip(),
        encoding="utf-8",
    )

    service = AReaLService(sft_config=AReaLSFTConfig.from_yaml(service_config))

    assert service._sft_wrapper is not None
    assert service._sft_wrapper.config.commands == ("python", "examples.custom_sft.py")
    assert service._sft_wrapper.config.environment_variables == {"NCCL_DEBUG": "WARN"}
