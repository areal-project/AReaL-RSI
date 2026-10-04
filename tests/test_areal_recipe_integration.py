"""Optional CPU contract test against an adjacent AReaL 2.0 checkout."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest
from areal_rsi_tir.contracts import TirTraceBundle
from areal_rsi_tir.evaluate import main as eval_main
from areal_rsi_tir.harness import freeze_tir_harness
from areal_rsi_tir.tasks import TirSplitSizes, freeze_tir_task_set
from areal_rsi_tir.train_rl import main as train_main

from areal_rsi.api.control import read_record
from areal_rsi.api.records import RsiEvaluationReport
from areal_rsi.api.research import RsiTrainResult
from areal_rsi.infra.storage.artifacts import checkpoint_digest
from areal_rsi.scripts.jobs import digest_bytes


def _math_inputs(
    tmp_path: Path, runner: Path, *, evolve_size: int = 1
) -> tuple[Path, Path, Path, Path]:
    train = tmp_path / "train.jsonl"
    test = tmp_path / "test.jsonl"

    def case(index: int) -> str:
        return json.dumps(
            {
                "messages": [{"role": "user", "content": f"What is {index}+1?"}],
                "answer": str(index + 1),
            }
        )

    train.write_text("\n".join(case(index) for index in range(3 + evolve_size)) + "\n")
    test.write_text(case(3 + evolve_size) + "\n")
    task_root = tmp_path / "taskset"
    freeze_tir_task_set(train, test, task_root, sizes=TirSplitSizes(evolve_size, 1, 1, 1), seed=42)
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Solve with calculator. {prompt} Final answer in \\boxed{{}}.")
    harness_root = tmp_path / "h0"
    freeze_tir_harness(prompt, runner, harness_root)
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text('{"model_type":"gpt2"}')
    (model / "model.safetensors").write_bytes(b"fake-model")
    return task_root, harness_root, model, prompt


@pytest.mark.skipif(not os.environ.get("AREAL_CHECKOUT"), reason="AReaL checkout not configured")
@pytest.mark.parametrize("scheduler_type", ["slurm", "local"])
def test_eval_recipe_writes_linked_report_and_trajectories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scheduler_type: str,
) -> None:
    import areal.engine  # type: ignore[import-not-found]
    import areal.infra  # type: ignore[import-not-found]
    import areal.utils.hf_utils  # type: ignore[import-not-found]
    import torch  # type: ignore[import-not-found]
    from areal.infra.staleness_manager import StalenessManager  # type: ignore[import-not-found]

    checkout = Path(os.environ["AREAL_CHECKOUT"]).resolve()
    runner = checkout / "examples/tir"
    assert runner.is_dir()
    task_root, harness_root, model, prompt = _math_inputs(tmp_path, runner, evolve_size=40)

    class CharTokenizer:
        def encode(self, value: str, **_kwargs: Any) -> list[int]:
            return [ord(character) for character in value]

        def decode(self, ids: list[int], **_kwargs: Any) -> str:
            return "".join(chr(token) for token in ids)

    tokenizer = CharTokenizer()

    class FakeController:
        def __init__(self) -> None:
            self.submissions: list[dict[str, Any]] = []
            self.destroyed = False
            self.staleness: Any = None

        def get_version(self) -> int:
            return 0

        def initialize(self, *, role: str, server_args: Any) -> None:
            assert role == "eval-rollout" and server_args is not None

        def submit(self, data: dict[str, Any], **kwargs: Any) -> int:
            assert kwargs["workflow"] == "areal_rsi_tir.workflow.FrozenTIRWorkflow"
            assert kwargs["group_size"] == 1 and kwargs["is_eval"]
            assert kwargs["task_id"] == len(self.submissions)
            self.submissions.append(data)
            return len(self.submissions) - 1

        def wait(self, count: int, timeout: None) -> list[dict[str, Any]]:
            assert count == len(self.submissions)
            assert timeout is None
            results = []
            for data in self.submissions:
                # Exercise AReaL's actual admission accounting beyond the
                # training batch size, without advancing the model version.
                assert self.staleness.get_capacity() > 0
                self.staleness.on_rollout_enqueued()
                self.staleness.on_rollout_submitted()
                self.staleness.on_rollout_accepted()
                question = data["messages"][0]["content"]
                initial = tokenizer.encode(prompt.read_text().format(prompt=question))
                generated = tokenizer.encode(data["answer"])
                results.append(
                    {
                        "input_ids": torch.tensor([initial + generated]),
                        "loss_mask": torch.tensor([[0] * len(initial) + [1] * len(generated)]),
                        "attention_mask": torch.ones(
                            (1, len(initial) + len(generated)), dtype=torch.bool
                        ),
                        "rewards": torch.tensor([1.0]),
                    }
                )
            return results

        def destroy(self) -> None:
            self.destroyed = True

    controller = FakeController()

    class FakeEngineType:
        @staticmethod
        def as_controller(rollout: Any, _scheduler: Any) -> FakeController:
            assert rollout.deterministic_sampling
            controller.staleness = StalenessManager(
                controller,
                max_concurrent_rollouts=16,
                consumer_batch_size=rollout.consumer_batch_size,
                max_staleness=rollout.max_head_offpolicyness,
            )
            return controller

    monkeypatch.setattr(areal.engine, "RemoteSGLangEngine", FakeEngineType)
    monkeypatch.setattr(
        areal.infra,
        "LocalScheduler" if scheduler_type == "local" else "SlurmScheduler",
        lambda exp_config: object(),
    )
    monkeypatch.setattr(areal.utils.hf_utils, "load_hf_tokenizer", lambda _path: tokenizer)
    output = tmp_path / "evaluation.json"
    config = Path(__file__).resolve().parents[1] / "examples/areal_rsi_tir/tir_grpo.yaml"
    eval_main(
        [
            "--task-set-root",
            str(task_root),
            "--harness-root",
            str(harness_root),
            "--base-model-digest",
            checkpoint_digest(model),
            "--split",
            "harness_evolve",
            "--output",
            str(output),
            "--config",
            str(config),
            f"scheduler.type={scheduler_type}",
            "trial_name=cpu_eval",
            f"actor.path={model}",
            f"ref.path={model}",
            f"tokenizer_path={model}",
            f"sglang.model_path={model}",
            "tir.enable_tools=calculator",
            f"train_dataset.path={task_root / 'public/model_train.jsonl'}",
            f"valid_dataset.path={task_root / 'public/model_dev.jsonl'}",
            "gconfig.n_samples=1",
            "actor.reward_norm=null",
        ]
    )
    report = read_record(output, RsiEvaluationReport)
    bundle = read_record(output.with_suffix(".trajectories.json"), TirTraceBundle)
    assert controller.destroyed
    assert len(controller.submissions) == len(report.cases) == len(bundle.trajectories) == 40
    assert all(case.score == 1.0 for case in report.cases)
    assert all(trace.attention_mask[0] == 1 for trace in bundle.trajectories)
    assert report.trajectory_digest == digest_bytes(
        output.with_suffix(".trajectories.json").read_bytes()
    )


@pytest.mark.skipif(not os.environ.get("AREAL_CHECKOUT"), reason="AReaL checkout not configured")
@pytest.mark.parametrize("epochs", [1, 3])
def test_grpo_recipe_writes_checkpoint_and_task_covered_rollout_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    epochs: int,
) -> None:
    import areal  # type: ignore[import-not-found]
    import areal.utils.hf_utils  # type: ignore[import-not-found]
    from areal.utils.saver import Saver  # type: ignore[import-not-found]

    runner = Path(os.environ["AREAL_CHECKOUT"]).resolve() / "examples/tir"
    task_root, harness_root, model, _prompt = _math_inputs(tmp_path, runner)

    class CharTokenizer:
        def encode(self, value: str, **_kwargs: Any) -> list[int]:
            return [ord(character) for character in value]

    class FakeTrainer:
        def __init__(self, config: Any, train_dataset: Any, valid_dataset: Any) -> None:
            self.config = config
            self.train_dataset = train_dataset
            assert len(train_dataset) == len(valid_dataset) == 1

        def __enter__(self) -> FakeTrainer:
            return self

        def __exit__(self, *_args: Any) -> None:
            pass

        def train(self, **kwargs: Any) -> None:
            assert kwargs["workflow"] == "areal_rsi_tir.workflow.FrozenTIRWorkflow"
            assert kwargs["workflow_kwargs"]["task_set_root"] == str(task_root)
            assert kwargs["workflow_kwargs"]["model_digest"] == checkpoint_digest(model)
            checkpoint = Path(
                Saver.get_model_save_path(
                    self.config.saver.experiment_name,
                    self.config.saver.trial_name,
                    self.config.saver.fileroot,
                    epoch=epochs - 1,
                    step=0,
                    globalstep=epochs - 1,
                )
            )
            checkpoint.mkdir(parents=True, exist_ok=True)
            for source in model.iterdir():
                shutil.copy2(source, checkpoint / source.name)
            (checkpoint / "config.json").write_text('{"model_type":"gpt2","step":0}')
            rollout = (
                Path(self.config.rollout.fileroot)
                / "logs/worker"
                / self.config.rollout.experiment_name
                / self.config.rollout.trial_name
                / "rollout/1"
            )
            rollout.mkdir(parents=True)
            (rollout / "7.jsonl").write_text(
                json.dumps({"source_task_id": self.train_dataset[0]["task_id"], "reward": 1.0})
                + "\n"
            )

    monkeypatch.setattr(areal, "PPOTrainer", FakeTrainer)
    monkeypatch.setattr(areal.utils.hf_utils, "load_hf_tokenizer", lambda _path: CharTokenizer())
    config = Path(__file__).resolve().parents[1] / "examples/areal_rsi_tir/tir_grpo.yaml"
    receipt_path = tmp_path / "train.json"
    train_args = [
        "--task-set-root",
        str(task_root),
        "--harness-root",
        str(harness_root),
        "--base-model-digest",
        checkpoint_digest(model),
        "--run-id",
        "cpu_train",
        "--receipt",
        str(receipt_path),
        "--config",
        str(config),
        "scheduler.type=slurm",
        "trial_name=cpu_train",
        f"cluster.fileroot={tmp_path / 'experiments'}",
        f"cluster.name_resolve.nfs_record_root={tmp_path / 'name_resolve'}",
        f"actor.path={model}",
        f"ref.path={model}",
        f"tokenizer_path={model}",
        f"sglang.model_path={model}",
        "tir.enable_tools=calculator",
        f"train_dataset.path={task_root / 'public/model_train.jsonl'}",
        f"valid_dataset.path={task_root / 'public/model_dev.jsonl'}",
        f"total_train_epochs={epochs}",
        f"saver.freq_epochs={epochs}",
    ]
    if epochs > 1:
        with pytest.raises(ValueError, match="one final Saver checkpoint"):
            train_main([*train_args, "saver.freq_epochs=1"])
    train_main(train_args)
    receipt = read_record(receipt_path, RsiTrainResult)
    assert receipt.algorithm == "grpo" and receipt.global_step == epochs
    assert receipt.rollout.records == receipt.rollout.tasks == 1
    assert receipt.checkpoint_digest != receipt.base_model_digest
    assert receipt_path.with_suffix(".intent.json").is_file()

    receipt_bytes = receipt_path.read_bytes()
    receipt_path.unlink()
    monkeypatch.setattr(areal, "PPOTrainer", lambda *_args: pytest.fail("retrained"))
    train_main(["--reconcile-only", *train_args])
    assert receipt_path.read_bytes() == receipt_bytes

    receipt_path.unlink()
    with pytest.raises(ValueError, match="original training intent"):
        train_main(["--reconcile-only", *train_args, "actor.optimizer.lr=0.000123"])
