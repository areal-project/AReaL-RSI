"""Capture actual native reward arguments with deterministic generation fixtures."""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from typing import Any

import pytest

from areal_rsi.api.execution import AReaLRuntime
from areal_rsi.api.records import TraceEvent
from examples.tir.benchmark import Example, evaluation_cases, identity, write_examples
from examples.tir.pipeline import TIRConfig, WorkerRequest
from examples.tir.reward import native_reward_input

pytest.importorskip("tir_workflow", reason="Requires the native AReaL TIR runtime")


class CharacterTokenizer:
    pad_token_id = 0
    eos_token_id = 1

    def encode(self, text: str, add_special_tokens: bool = False) -> list[int]:
        return list(map(ord, text))

    def decode(self, tokens: list[int]) -> str:
        return "".join(map(chr, tokens))


@pytest.mark.parametrize(
    "segments",
    [
        ("<calculator>", "2+2</calculator>", r"The answer is \boxed{4}."),
        ("<calculator>", "1/0</calculator>", r"The answer is \boxed{4}."),
        ("<calculator>", "2+2", "</calculator>", r"The answer is \boxed{4}."),
    ],
)
def test_replay_equals_actual_native_reward_argument(segments: tuple[str, ...]) -> None:
    """Successful, failed, and initially unroutable tool calls replay byte exactly."""
    api = importlib.import_module("areal.api")
    config = importlib.import_module("areal.api.cli_args")
    tools = importlib.import_module("tool_manager")
    recorded = importlib.import_module("examples.tir.workflow")
    tokenizer = CharacterTokenizer()
    generated = iter(enumerate(segments))
    captured: list[str] = []

    class GenerationFixture:
        async def agenerate(self, request: Any) -> Any:
            index, text = next(generated)
            tokens = tokenizer.encode(text)
            if index == len(segments) - 1:
                tokens.append(tokenizer.eos_token_id)
            return api.ModelResponse(
                input_tokens=request.input_ids,
                output_tokens=tokens,
                output_logprobs=[0.0] * len(tokens),
                output_versions=[0] * len(tokens),
                stop_reason="stop",
            )

    async def capture(
        prompt: str,
        completions: str,
        prompt_ids: object,
        completion_ids: object,
        **kwargs: object,
    ) -> float:
        captured.append(completions)
        return 0.0

    async def run() -> tuple[TraceEvent, ...]:
        manager = tools.ToolManager(enabled_tools="python;calculator")
        workflow = object.__new__(recorded.RecordedTIRWorkflow)
        workflow.tokenizer = tokenizer
        workflow.gconfig = config.GenerationHyperparameters(max_new_tokens=512)
        workflow.max_length = 3000
        workflow.max_turns = 2
        workflow.tool_manager = manager
        workflow.start_markers = manager.get_all_start_markers()
        workflow.end_markers = manager.get_all_end_markers()
        workflow.async_reward_fn = capture
        events: list[TraceEvent] = []
        token = recorded._events.set(events)
        try:
            await workflow._multi_round_response(
                GenerationFixture(), tokenizer.encode("fixed prompt"), {"answer": "4"}, manager
            )
            return tuple(events)
        finally:
            recorded._events.reset(token)
            await manager.acleanup()

    events = asyncio.run(run())
    assert len(captured) == 1
    assert native_reward_input(events) == captured[0]
    assert captured[0] != "".join(segments)
    assert sum(event.kind == "tool_call" for event in events) == (
        2 if len(segments) == 4 else 1
    )


@pytest.mark.parametrize(
    "events",
    [
        (),
        (TraceEvent("generation", "missing prompt"),),
        (TraceEvent("prompt", "prompt"), TraceEvent("tool_result", "unpaired")),
        (
            TraceEvent("prompt", "prompt"),
            TraceEvent("generation", "<calculator>"),
            TraceEvent("tool_call", "<calculator>2+2</calculator>"),
        ),
    ],
)
def test_incomplete_trace_cannot_be_regraded(events: tuple[TraceEvent, ...]) -> None:
    with pytest.raises(ValueError):
        native_reward_input(events)


def test_native_worker_only_executes_requested_replicate_ids(tmp_path: Path) -> None:
    worker = importlib.import_module("examples.tir.worker")
    prompt = "Two plus two in a fixed recovery case"
    item = Example(identity(prompt), prompt, "4", "torl-train", repetitions=4)
    path = tmp_path / "questions.jsonl"
    write_examples(path, (item,))
    cases = evaluation_cases((item,))
    request = WorkerRequest(
        "eval", TIRConfig(AReaLRuntime("unused-backend", "unused-python"), "unused-model"),
        "unused-model", str(path), str(path), "unused-output", "recovery-fixture",
        "benchmark", (cases[3].task_id, cases[1].task_id),
    )
    actual = worker.dataset(str(path), request)
    assert list(actual["task_id"]) == [cases[1].task_id, cases[3].task_id]
    assert list(actual["answer"]) == ["4", "4"]
