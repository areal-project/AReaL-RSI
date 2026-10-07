"""Optional CPU contract test against an adjacent AReaL 2.0 checkout."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from areal_rsi.api.control import json_bytes, read_record
from areal_rsi.api.records import RsiEvaluationReport
from areal_rsi.api.research import RsiTrainResult
from areal_rsi.examples.classification import PROFILE as CLASSIFICATION_PROFILE
from areal_rsi.examples.tir.profile import PROFILE
from areal_rsi.infra.coordination.evaluation.contracts import TraceBundle
from areal_rsi.infra.coordination.evaluation.evaluate import main as eval_main
from areal_rsi.infra.coordination.evaluation.harness import freeze_prompt_harness
from areal_rsi.infra.coordination.evaluation.profile import PromptProfile
from areal_rsi.infra.coordination.evaluation.tasks import SplitSizes, freeze_task_set
from areal_rsi.infra.storage.artifacts import checkpoint_digest
from areal_rsi.scripts.jobs import digest_bytes
from areal_rsi.scripts.train import main as train_main


def _prompt_inputs(
    tmp_path: Path, runner: Path, profile: PromptProfile, *, evolve_size: int = 1
) -> tuple[Path, Path, Path, Path]:
    train = tmp_path / "train.jsonl"
    test = tmp_path / "test.jsonl"

    def case(index: int) -> str:
        return json.dumps(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": f"What is {index}+1?"
                        if profile.enabled_tools
                        else f"Review {index}: a wonderful day",
                    }
                ],
                "answer": str(index + 1) if profile.enabled_tools else "positive",
            }
        )

    train.write_text("\n".join(case(index) for index in range(3 + evolve_size)) + "\n")
    test.write_text(case(3 + evolve_size) + "\n")
    task_root = tmp_path / "taskset"
    freeze_task_set(train, test, task_root, sizes=SplitSizes(evolve_size, 1, 1, 1), seed=42)
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Solve with calculator. {prompt} Final answer in \\boxed{{}}."
        if profile.enabled_tools
        else "Return a sentiment label. {prompt}"
    )
    harness_root = tmp_path / "h0"
    freeze_prompt_harness(prompt, runner, harness_root, enabled_tools=profile.enabled_tools)
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text('{"model_type":"gpt2"}')
    (model / "model.safetensors").write_bytes(b"fake-model")
    return task_root, harness_root, model, prompt


@pytest.mark.skipif(not os.environ.get("AREAL_CHECKOUT"), reason="AReaL checkout not configured")
@pytest.mark.parametrize(
    "profile", [PROFILE, CLASSIFICATION_PROFILE], ids=["tir", "classification"]
)
def test_eval_recipe_writes_linked_report_and_trajectories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    profile: PromptProfile,
) -> None:
    import areal.engine  # type: ignore[import-not-found]
    import areal.infra  # type: ignore[import-not-found]
    import areal.utils.hf_utils  # type: ignore[import-not-found]
    import torch  # type: ignore[import-not-found]
    from areal.infra.staleness_manager import StalenessManager  # type: ignore[import-not-found]

    checkout = Path(os.environ["AREAL_CHECKOUT"]).resolve()
    runner = checkout / profile.runner_directory
    assert runner.is_dir()
    task_root, harness_root, model, prompt = _prompt_inputs(
        tmp_path, runner, profile, evolve_size=40
    )

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
            assert kwargs["workflow"] == profile.workflow
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
        "SlurmScheduler",
        lambda exp_config: object(),
    )
    monkeypatch.setattr(areal.utils.hf_utils, "load_hf_tokenizer", lambda _path: tokenizer)
    output = tmp_path / "evaluation.json"
    config = Path(__file__).resolve().parents[1] / "src/areal_rsi/examples/grpo.yaml"
    profile_path = tmp_path / "profile.json"
    profile_path.write_bytes(json_bytes(profile))
    eval_main(
        [
            "--profile",
            str(profile_path),
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
            "scheduler.type=slurm",
            "trial_name=cpu_eval",
            f"actor.path={model}",
            f"ref.path={model}",
            f"tokenizer_path={model}",
            f"sglang.model_path={model}",
            *(("++tir.enable_tools=calculator",) if profile.enabled_tools else ()),
            f"train_dataset.path={task_root / 'public/model_train.jsonl'}",
            f"valid_dataset.path={task_root / 'public/model_dev.jsonl'}",
            "gconfig.n_samples=1",
            "actor.reward_norm=null",
        ]
    )
    report = read_record(output, RsiEvaluationReport)
    bundle = read_record(output.with_suffix(".trajectories.json"), TraceBundle)
    assert controller.destroyed
    assert len(controller.submissions) == len(report.cases) == len(bundle.trajectories) == 40
    assert all(case.score == 1.0 for case in report.cases)
    assert all(trace.attention_mask[0] == 1 for trace in bundle.trajectories)
    assert report.trajectory_digest == digest_bytes(
        output.with_suffix(".trajectories.json").read_bytes()
    )


@pytest.mark.skipif(not os.environ.get("AREAL_CHECKOUT"), reason="AReaL checkout not configured")
@pytest.mark.parametrize(
    "profile", [PROFILE, CLASSIFICATION_PROFILE], ids=["tir", "classification"]
)
@pytest.mark.parametrize("epochs", [1, 3])
def test_grpo_recipe_writes_checkpoint_and_task_covered_rollout_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    epochs: int,
    profile: PromptProfile,
) -> None:
    import areal  # type: ignore[import-not-found]
    import areal.utils.hf_utils  # type: ignore[import-not-found]
    from areal.utils.saver import Saver  # type: ignore[import-not-found]

    runner = Path(os.environ["AREAL_CHECKOUT"]).resolve() / profile.runner_directory
    task_root, harness_root, model, _prompt = _prompt_inputs(tmp_path, runner, profile)

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
            assert kwargs["workflow"] == profile.workflow
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
    config = Path(__file__).resolve().parents[1] / "src/areal_rsi/examples/grpo.yaml"
    receipt_path = tmp_path / "train.json"
    profile_path = tmp_path / "profile.json"
    profile_path.write_bytes(json_bytes(profile))
    train_args = [
        "--recipe",
        "areal_rsi.infra.coordination.evaluation.train.prepare",
        "--profile",
        str(profile_path),
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
        *(("++tir.enable_tools=calculator",) if profile.enabled_tools else ()),
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


@pytest.mark.skipif(not os.environ.get("AREAL_CHECKOUT"), reason="AReaL checkout not configured")
@pytest.mark.parametrize(
    "profile", [PROFILE, CLASSIFICATION_PROFILE], ids=["tir", "classification"]
)
def test_native_workflow_formats_frozen_prompt_and_rejects_changed_snapshot(
    tmp_path: Path,
    profile: PromptProfile,
) -> None:
    import importlib

    from areal.api.cli_args import GenerationHyperparameters  # type: ignore[import-not-found]

    from areal_rsi.infra.coordination.evaluation.assets import load_frozen_inputs, verifier_sources
    from areal_rsi.infra.coordination.evaluation.profile import workflow_kwargs
    from areal_rsi.infra.storage.artifacts import source_files_digest
    from areal_rsi.scripts.train import load_config

    runner = Path(os.environ["AREAL_CHECKOUT"]).resolve() / profile.runner_directory
    task_root, harness_root, model, prompt = _prompt_inputs(tmp_path, runner, profile)
    frozen = load_frozen_inputs(
        task_root, harness_root, runner, boxed_answers=profile.boxed_answers
    )
    config_file = Path(__file__).resolve().parents[1] / "src/areal_rsi/examples/grpo.yaml"
    config = load_config(
        ["--config", str(config_file), f"actor.path={model}"],
        profile.config_class,
    )
    kwargs = workflow_kwargs(
        profile,
        config,
        prompt_template=frozen.prompt_template,
        task_set_root=task_root,
        harness_root=harness_root,
        task_set_id=frozen.task_set.task_set_id,
        harness_id=frozen.harness.harness_id,
        runner_digest=frozen.harness.runner_digest,
        verifier_digest=source_files_digest(verifier_sources(runner)),
        model_digest=checkpoint_digest(model),
    )

    class CharTokenizer:
        pad_token_id = 0
        eos_token_id = 1

        def encode(self, value: str, **_kwargs: Any) -> list[int]:
            return [ord(character) for character in value]

    kwargs["tokenizer"] = CharTokenizer()
    kwargs["gconfig"] = GenerationHyperparameters()
    if profile.enabled_tools:
        config.tir.enable_tools = "calculator"
    module, name = profile.workflow.rsplit(".", 1)
    workflow_type = getattr(importlib.import_module(module), name)
    workflow = workflow_type(**kwargs)
    from areal.v2.inference_service.controller.controller import RolloutControllerV2

    controller = SimpleNamespace(external_mode=False, _wrap_agent=lambda agent, **_kwargs: agent)
    assert RolloutControllerV2._resolve_workflow(controller, workflow) is workflow
    question = frozen.train_rows[0]["messages"][0]["content"]
    assert workflow.messages(frozen.train_rows[0])[0]["content"] == prompt.read_text().format(
        prompt=question
    )
    kwargs["model_digest"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="model snapshot"):
        workflow_type(**kwargs)


@pytest.mark.skipif(not os.environ.get("AREAL_CHECKOUT"), reason="AReaL checkout not configured")
@pytest.mark.parametrize(
    ("reward", "answer"),
    [
        ("areal_rsi.infra.coordination.evaluation.rlvr.exact_reward", "positive"),
        ("areal_rsi.infra.coordination.evaluation.tir.verifier.arithmetic_reward", "3"),
    ],
)
def test_native_v2_prompt_agent_uses_proxy_and_scores_correct_and_wrong_answers(
    reward: str,
    answer: str,
) -> None:
    import asyncio

    import httpx
    from areal.api.cli_args import GenerationHyperparameters
    from areal.v2.inference_service.controller.controller import RolloutControllerV2

    from areal_rsi.infra.runtime.prompt import PromptRewardAgent

    class Tokenizer:
        def encode(self, value: str, **_kwargs: Any) -> list[int]:
            return [ord(c) for c in value]

        def apply_chat_template(self, messages: Any, **_kwargs: Any) -> str:
            return "".join(message["content"] for message in messages)

    replies = iter([answer, "wrong"])
    requests: list[dict[str, Any]] = []

    def respond(request: Any) -> Any:
        assert request.headers["authorization"] == "Bearer test-session"
        assert request.url.path == "/chat/completions"
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload["n"] == 1
        assert payload["max_completion_tokens"] == 64
        assert payload["max_total_tokens"] == 512
        assert payload["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False
        return httpx.Response(
            200,
            json={
                "id": "test-completion",
                "object": "chat.completion",
                "created": 0,
                "model": "default",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": next(replies)},
                    }
                ],
            },
        )

    agent = PromptRewardAgent(
        reward_fn=reward,
        tokenizer=Tokenizer(),
        gconfig=GenerationHyperparameters(n_samples=2, max_tokens=512, max_new_tokens=64),
    )
    controller = SimpleNamespace(external_mode=False, _wrap_agent=lambda agent, **_kwargs: agent)
    assert RolloutControllerV2._resolve_workflow(controller, agent) is agent

    async def run() -> list[float]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            data = {"messages": [{"role": "user", "content": "question"}], "answer": answer}
            return [
                await agent.run(
                    data, base_url="http://proxy", api_key="test-session", http_client=client
                )
                for _ in range(2)
            ]

    assert asyncio.run(run()) == [1.0, 0.0]
    assert requests[0]["messages"] == [{"role": "user", "content": "question"}]


@pytest.mark.skipif(not os.environ.get("AREAL_CHECKOUT"), reason="AReaL checkout not configured")
@pytest.mark.parametrize("generation_fails", [False, True])
def test_native_v2_calculator_agent_keeps_tool_result_and_cleans_up_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    generation_fails: bool,
) -> None:
    import asyncio

    import httpx
    from areal.api.cli_args import GenerationHyperparameters
    from examples.tir.tool_manager import ToolManager

    from areal_rsi.infra.runtime.prompt import PromptRewardAgent
    from areal_rsi.infra.runtime.tir import FrozenTIRWorkflow

    class Tokenizer:
        def encode(self, value: str, **_kwargs: Any) -> list[int]:
            return [ord(c) for c in value]

        def apply_chat_template(self, messages: Any, **_kwargs: Any) -> str:
            return "".join(message["content"] for message in messages)

    agent = object.__new__(FrozenTIRWorkflow)
    PromptRewardAgent.__init__(
        agent,
        reward_fn="areal_rsi.infra.coordination.evaluation.tir.verifier.strict_tir_reward_fn",
        tokenizer=Tokenizer(),
        prompt_template="User: {prompt}\nAssistant:",
        gconfig=GenerationHyperparameters(max_new_tokens=64, max_tokens=512),
    )
    agent.tir_config = SimpleNamespace(
        max_turns=2,
        max_length=256,
        tool_timeout=5,
        enable_tools="calculator",
    )
    cleaned: list[bool] = []
    original_cleanup = ToolManager.acleanup

    async def cleanup(manager: Any) -> None:
        cleaned.append(True)
        await original_cleanup(manager)

    monkeypatch.setattr(ToolManager, "acleanup", cleanup)
    requests: list[dict[str, Any]] = []

    def respond(request: Any) -> Any:
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload["max_total_tokens"] == 256
        if len(requests) == 1:
            text = "<calculator>1+2</calculator>"
            assert payload["messages"] == [{"role": "user", "content": "User: 1+2\nAssistant:"}]
        else:
            assert payload["messages"][-1] == {"role": "user", "content": "\n```output\n3\n```\n"}
            if generation_fails:
                raise httpx.ReadTimeout("generation timeout", request=request)
            text = "\\boxed{3}"
        return httpx.Response(
            200,
            json={
                "id": "test-completion",
                "object": "chat.completion",
                "created": 0,
                "model": "default",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": text},
                    }
                ],
            },
        )

    async def run() -> float:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await agent.run(
                {"messages": [{"role": "user", "content": "1+2"}], "answer": "3"},
                base_url="http://proxy",
                api_key="test-session",
                http_client=client,
            )

    if generation_fails:
        from openai import APITimeoutError

        with pytest.raises(APITimeoutError):
            asyncio.run(run())
    else:
        assert asyncio.run(run()) == 1.0
    assert len(requests) == 2 and cleaned == [True]
