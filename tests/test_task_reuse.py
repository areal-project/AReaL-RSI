"""Dataset/model changes reuse the same preparation and AReaL training boundary."""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from areal_rsi.infra.coordination.evaluation import rlvr
from areal_rsi.scripts.train import TrainingPlan, train, validate_v2


@pytest.mark.parametrize(
    ("recipe", "model", "prompt", "answer"),
    [
        ("arithmetic", "/models/small-math", "What is 1+2?", "3"),
        ("classification", "/models/large-chat", "A wonderful movie", "positive"),
    ],
)
def test_dataset_and_model_swap_only_changes_example_recipe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recipe: str,
    model: str,
    prompt: str,
    answer: str,
) -> None:
    data = tmp_path / "data.jsonl"
    data.write_text(json.dumps({"task_id": "one", "prompt": prompt, "answer": answer}) + "\n")
    config = SimpleNamespace(
        scheduler=SimpleNamespace(type="slurm"),
        actor=SimpleNamespace(path=model, _version="v2"),
        ref=SimpleNamespace(path=model),
        rollout=SimpleNamespace(_version="v2"),
        sglang=SimpleNamespace(model_path=model),
        tokenizer_path=model,
        train_dataset=SimpleNamespace(batch_size=1),
        gconfig=object(),
    )
    monkeypatch.setattr(rlvr, "load_config", lambda argv: config)
    plan = importlib.import_module(f"areal_rsi.examples.{recipe}").prepare(["--data", str(data)])
    assert plan is not None
    observed: list[float] = []

    class Dataset:
        @staticmethod
        def from_list(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return rows

    class Trainer:
        def __init__(self, settings: Any, rows: list[dict[str, Any]], valid_dataset: None) -> None:
            assert settings.actor.path == model and valid_dataset is None
            self.rows = rows

        def __enter__(self) -> Trainer:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def train(self, *, workflow: str, workflow_kwargs: dict[str, Any]) -> None:
            assert workflow == "areal.workflow.rlvr.RLVRWorkflow"
            assert self.rows[0]["messages"] == [{"role": "user", "content": prompt}]
            module, name = workflow_kwargs["reward_fn"].rsplit(".", 1)
            reward = getattr(importlib.import_module(module), name)
            observed.append(reward(prompt, answer, [], [], self.rows[0]["answer"]))
            observed.append(reward(prompt, "wrong", [], [], self.rows[0]["answer"]))

    monkeypatch.setitem(sys.modules, "areal", SimpleNamespace(PPOTrainer=Trainer))
    monkeypatch.setitem(sys.modules, "datasets", SimpleNamespace(Dataset=Dataset))
    train(plan)
    assert observed == [1.0, 0.0]


def test_training_rejects_unsupported_deployment_before_trainer_import() -> None:
    config = SimpleNamespace(
        scheduler=SimpleNamespace(type="local"),
        actor=SimpleNamespace(_version="v2"),
        rollout=SimpleNamespace(_version="v2"),
    )
    with pytest.raises(ValueError, match="Slurm"):
        validate_v2(config)
    config.scheduler.type = "slurm"
    config.actor._version = "v1"
    with pytest.raises(ValueError, match=r"actor\._version=v2"):
        validate_v2(config)


def test_duplicate_dataset_identity_is_rejected(tmp_path: Path) -> None:
    data = tmp_path / "data.jsonl"
    row = json.dumps({"task_id": "one", "prompt": "A wonderful movie", "answer": "positive"})
    data.write_text(row + "\n" + row + "\n")
    with pytest.raises(ValueError, match="unique task identities"):
        rlvr.dataset_rows(data)


@pytest.mark.parametrize("worker_mounts", [None, "/shared:/shared,/models:/models"])
def test_worker_startup_does_not_inherit_controller_container_mounts(
    monkeypatch: pytest.MonkeyPatch, worker_mounts: str | None
) -> None:
    controller_mounts = "/container/libmunge.so.2:/container/libmunge.so.2"
    monkeypatch.setenv("SINGULARITY_BIND", controller_mounts)
    monkeypatch.delenv("APPTAINER_BIND", raising=False)
    if worker_mounts is None:
        monkeypatch.delenv("WORKER_MOUNTS", raising=False)
    else:
        monkeypatch.setenv("WORKER_MOUNTS", worker_mounts)
    config = SimpleNamespace(
        scheduler=SimpleNamespace(type="slurm"),
        actor=SimpleNamespace(_version="v2"),
        rollout=SimpleNamespace(_version="v2"),
    )

    class Dataset:
        @staticmethod
        def from_list(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return rows

    class FailedTrainer:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            assert os.environ.get("SINGULARITY_BIND") == worker_mounts
            assert os.environ.get("APPTAINER_BIND") == worker_mounts
            raise RuntimeError("worker startup failure")

    monkeypatch.setitem(sys.modules, "areal", SimpleNamespace(PPOTrainer=FailedTrainer))
    monkeypatch.setitem(sys.modules, "datasets", SimpleNamespace(Dataset=Dataset))
    plan = TrainingPlan(config, ({"prompt": "one"},), None, "workflow", {})
    with pytest.raises(RuntimeError, match="worker startup failure"):
        train(plan)
    assert os.environ["SINGULARITY_BIND"] == controller_mounts
    assert "APPTAINER_BIND" not in os.environ
