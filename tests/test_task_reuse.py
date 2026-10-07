"""Dataset/model changes reuse the same preparation and AReaL training boundary."""

from __future__ import annotations

import importlib
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from areal_rsi.api.control import json_bytes
from areal_rsi.infra.coordination import continuation
from areal_rsi.infra.coordination.evaluation import rlvr
from areal_rsi.infra.coordination.evaluation.tasks import load_task_set
from areal_rsi.infra.storage.artifacts import checkpoint_digest
from areal_rsi.scripts.train import TrainingPlan, train, validate_v2


def test_completed_first_training_is_reused_without_rewriting_its_frozen_evidence(
    frozen_inputs: tuple[Path, Path, Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tasks, harness, _, model = frozen_inputs
    source_root = tmp_path / "parent-source"
    (source_root / "src").mkdir(parents=True)
    (source_root / "src/controller.py").write_text("# frozen parent controller\n")
    output = tmp_path / "parent-run"
    output.mkdir()
    template = tmp_path / "template.yaml"
    template.write_bytes(
        json_bytes(
            {
                "actor": {"scheduling_spec": [{"env_vars": {}}]},
                "ref": {},
                "rollout": {},
                "scheduler": {},
                "cluster": {"name_resolve": {}},
                "gconfig": {},
                "sglang": {},
                "train_dataset": {},
                "valid_dataset": {},
            }
        )
    )
    monkeypatch.delenv("RSI_SLURM_RESERVATION", raising=False)
    plan = continuation.ContinuationPlan(
        "paired",
        str(model),
        str(harness / "prompt.txt"),
        str(tasks),
        str(template),
        1,
        1,
        0.02,
        0.5,
        False,
        4096,
    )
    plan_path = tmp_path / "plan.json"
    plan_path.write_bytes(json_bytes(plan))
    lock = continuation.ContinuationSources(
        continuation.sources(source_root),
        continuation.digest(plan_path),
        continuation.digest(template),
        load_task_set(tasks).task_set_id,
        None,
    )
    lock_path = output / "source-lock.json"
    lock_path.write_bytes(json_bytes(lock))
    config = continuation.training_config(plan, output, model, 1, source_root=source_root)
    config_path = output / "train-g1.yaml"
    config_path.write_text(yaml.safe_dump(config))
    log_path = output / "g1-train.log"
    completed_log = (
        "Weight update completed for pair 'actor-rollout' v32 (74ms)\n"
        "Skipping rollout resume after final training step (global_step=31)\n"
        "Training completes!\n"
    )
    log_path.write_text(completed_log)
    search = output / "g1-search"
    (search / "model-candidates").mkdir(parents=True)
    (search / "selected-harness-path.txt").write_text(str(harness))
    (search / "model-candidates/decision.json").write_bytes(json_bytes({"decision": "reuse"}))
    (search / "m0-selected-dev.json").write_bytes(json_bytes({"saved": "before evaluation"}))
    checkpoint = output / (
        "areal-experiments/checkpoints/root/rsi-continuation-20261005/"
        "paired-g1/default/epoch1epochstep15globalstep31"
    )
    checkpoint.mkdir(parents=True)
    (checkpoint / "config.json").write_bytes(json_bytes({"model_type": "qwen2"}))
    shard = checkpoint / "model-00001-of-00001.safetensors"
    shard.write_bytes(b"completed-megatron-export")
    (checkpoint / "model.safetensors.index.json").write_bytes(
        json_bytes({"weight_map": {"layer": shard.name}})
    )
    receipt = continuation.CompletedFirstTraining(
        str(source_root),
        str(output),
        continuation.digest(plan_path),
        continuation.digest(lock_path),
        continuation.digest(config_path),
        continuation.digest(log_path),
        checkpoint_digest(checkpoint),
        continuation.search_digests(search),
    )
    before = {str(path): path.read_bytes() for path in output.rglob("*") if path.is_file()}
    assert continuation.reuse_first_training(plan, plan_path, receipt) == (search, checkpoint)
    assert before == {str(path): path.read_bytes() for path in output.rglob("*") if path.is_file()}

    # A completion marker alone must not authorize skipping unfinished training.
    log_path.write_text("Training completes!\n")
    with pytest.raises(ValueError, match="all 32 updates"):
        continuation.reuse_first_training(
            plan, plan_path, replace(receipt, log_digest=continuation.digest(log_path))
        )
    log_path.write_text(completed_log)
    config["total_train_epochs"] = 1
    config_path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match="configuration differs"):
        continuation.reuse_first_training(
            plan, plan_path, replace(receipt, config_digest=continuation.digest(config_path))
        )
    config["total_train_epochs"] = 2
    config_path.write_text(yaml.safe_dump(config))
    shard.write_bytes(b"changed-megatron-export")
    with pytest.raises(ValueError, match="checkpoint changed"):
        continuation.reuse_first_training(plan, plan_path, receipt)
    shard.write_bytes(b"completed-megatron-export")
    (search / "selected-harness-path.txt").write_text("different-harness")
    with pytest.raises(ValueError, match="search evidence changed"):
        continuation.reuse_first_training(plan, plan_path, receipt)
    (search / "selected-harness-path.txt").write_text(str(harness))

    # Exercise the real controller entry point: only post-training evaluation is
    # submitted, and provenance is written exclusively under the new output.
    train_ids = SimpleNamespace(task_ids=tuple(str(i) for i in range(512)))
    task_set = SimpleNamespace(
        task_set_id=lock.task_set_id,
        harness_evolve=train_ids,
        harness_select=train_ids,
        model_train=train_ids,
        model_dev=SimpleNamespace(task_ids=tuple(str(i) for i in range(256))),
    )
    monkeypatch.setattr(continuation, "load_task_set", lambda _: task_set)
    gpu_commands: list[list[str]] = []
    monkeypatch.setattr(continuation, "run_gpu", lambda command, _: gpu_commands.append(command))

    def reject_training(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("Completed first-round training must not be launched again")

    monkeypatch.setattr(continuation, "run", reject_training)
    result = continuation.RoundResult(
        1, 1.0, 2.0, 256, (), True, False, str(checkpoint), str(harness / "prompt.txt"), {}
    )
    monkeypatch.setattr(continuation, "decide", lambda *_args, **_kwargs: result)
    receipt_path = tmp_path / "completed-training.json"
    receipt_path.write_bytes(json_bytes(receipt))
    new_output = tmp_path / "new-execution"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "continuation",
            "--plan",
            str(plan_path),
            "--output",
            str(new_output),
            "--reuse-first-training",
            str(receipt_path),
        ],
    )
    continuation.main()
    assert len(gpu_commands) == 1
    assert gpu_commands[0][1].endswith("run_shared512_eval.sh")
    assert gpu_commands[0][2] == str(checkpoint)
    assert (new_output / "g1-training-reuse.json").read_bytes() == json_bytes(receipt)
    assert (new_output / "g1-result.json").read_bytes() == json_bytes(result)
    assert before == {str(path): path.read_bytes() for path in output.rglob("*") if path.is_file()}

    # A saved post-training evaluation is also reverified without GPU generation.
    saved_after = tmp_path / "saved-after.json"
    saved_after.write_bytes(json_bytes({"saved": "after evaluation"}))
    evaluation_path = tmp_path / "saved-evaluation.json"
    evaluation_path.write_bytes(
        json_bytes(
            continuation.SavedEvaluation(
                str(saved_after), continuation.digest(saved_after), str(source_root)
            )
        )
    )
    replayed: list[tuple[continuation.SavedEvaluation, Path]] = []

    def replay(saved: continuation.SavedEvaluation, _task_root: Path, path: Path, **_: Any) -> None:
        replayed.append((saved, path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(json_bytes({"original": saved.report_path}))

    monkeypatch.setattr(continuation, "reverify_claude_report", replay)
    monkeypatch.setattr(continuation, "freeze_claude_harness", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(continuation, "run_gpu", reject_training)
    decisions: list[Path | None] = []

    def decision(*_args: Any, before_path: Path | None = None) -> continuation.RoundResult:
        decisions.append(before_path)
        return result

    monkeypatch.setattr(continuation, "decide", decision)
    replay_output = tmp_path / "reverified-execution"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "continuation",
            "--plan",
            str(plan_path),
            "--output",
            str(replay_output),
            "--reuse-first-training",
            str(receipt_path),
            "--reuse-first-evaluation",
            str(evaluation_path),
        ],
    )
    continuation.main()
    assert [saved.report_path for saved, _ in replayed] == [
        str(search / "m0-selected-dev.json"),
        str(saved_after),
    ]
    assert decisions == [replay_output / "g1-before-reverified.json"]
    assert before == {str(path): path.read_bytes() for path in output.rglob("*") if path.is_file()}
    orphan_output = tmp_path / "orphan-evaluation"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "continuation",
            "--plan",
            str(plan_path),
            "--output",
            str(orphan_output),
            "--reuse-first-evaluation",
            str(evaluation_path),
        ],
    )
    with pytest.raises(SystemExit) as rejected:
        continuation.main()
    assert rejected.value.code == 2 and not orphan_output.exists()


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
            assert workflow == "areal_rsi.infra.runtime.prompt.PromptRewardAgent"
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
