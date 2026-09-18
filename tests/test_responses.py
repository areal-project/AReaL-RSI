"""Real Responses wire shapes with deterministic transports and crash recovery."""

from __future__ import annotations

import asyncio
import copy
import json
import tempfile
import unittest
from dataclasses import replace
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

from areal_rsi.coordination.config import config_from_dict
from areal_rsi.modules.responses import (
    STATE_PREFIX,
    EventSink,
    ResponsesConfig,
    ResponsesRuntime,
    ToolCall,
    ToolResult,
)

Json = dict[str, Any]


def answer(text: str, *, phase: str = "final_answer") -> Json:
    return {
        "type": "message",
        "id": "msg_example",
        "role": "assistant",
        "status": "completed",
        "phase": phase,
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def call(name: str, identifier: str, arguments: str = "{}") -> Json:
    return {
        "type": "function_call",
        "id": "fc_" + identifier,
        "call_id": identifier,
        "name": name,
        "arguments": arguments,
        "status": "completed",
    }


def response(*output: Json, status: str = "completed") -> Json:
    return {
        "id": "resp_example",
        "object": "response",
        "status": status,
        "output": list(output),
        "usage": {
            "input_tokens": 20,
            "output_tokens": 5,
            "total_tokens": 25,
            "output_tokens_details": {"reasoning_tokens": 3},
        },
    }


class ScriptedTransport:
    def __init__(self, *responses: Json):
        self.responses = list(responses)
        self.payloads: list[Json] = []

    async def request(self, config: ResponsesConfig, payload: Json, event_sink: EventSink) -> Json:
        self.payloads.append(copy.deepcopy(payload))
        if not self.responses:
            raise AssertionError("Unexpected model request, including a possible waiting poll")
        return self.responses.pop(0)


async def reject_call(call: ToolCall) -> ToolResult:
    raise AssertionError("Unexpected tool call: " + call.name)


@pytest.mark.parametrize(
    "model,window,measured_tokens,compacts",
    [
        ("gpt-6-astra", None, 180_000, False),
        ("gpt-6-astra", None, 220_000, True),
        ("unknown-gateway-model", None, 100_000, True),
        ("gpt-6-astra", 128_000, 100_000, True),
    ],
)
def test_model_context_budget_uses_measured_input_and_honors_overrides(
    tmp_path: Path, model: str, window: int | None, measured_tokens: int, compacts: bool
) -> None:
    async def scenario() -> None:
        first = response(answer("Original evidence remains archived."))
        first["usage"]["input_tokens"] = measured_tokens
        first["usage"]["total_tokens"] = measured_tokens + 5
        responses = [first]
        if compacts:
            responses.append(response(answer("Continuation checkpoint.")))
        responses.append(response(answer("Continued")))
        transport = ScriptedTransport(*responses)
        config = ResponsesConfig(
            base_url="https://example.test", api_key="test", model=model,
            context_window_tokens=window,
        )
        runtime = ResponsesRuntime(config, transport)
        for message in ("Begin", "Continue"):
            result = await runtime.turn(
                tmp_path, system_prompt="Research", user_message=message,
                tools=[], handler=reject_call,
            )
        assert result.text == "Continued"
        state = json.loads((tmp_path / "session.json").read_text())
        assert len(state["compactions"]) == int(compacts)
        assert len(transport.payloads) == 2 + int(compacts)
        assert "Original evidence remains archived." in json.dumps(state["history"])

    asyncio.run(scenario())


@pytest.mark.parametrize("window", [None, 128_000])
def test_research_context_budget_survives_pinning_and_configuration_recovery(
    monkeypatch: pytest.MonkeyPatch, window: int | None
) -> None:
    monkeypatch.setenv("RSI_API_KEY", "test")
    monkeypatch.setenv("RSI_MODEL", "gpt-6-astra")
    config = config_from_dict({
        "brief": {"objective": "Research", "instructions": "Improve an artifact"},
        "model": "unknown-gateway-model", "context_window_tokens": window,
    })
    assert config.runtime().effective_context_window_tokens == (window or 256_000)
    config.pin_model_settings()
    recovered = config_from_dict(config.public())
    monkeypatch.setenv("RSI_MODEL", "another-model")
    assert recovered.runtime().model == "gpt-6-astra"
    assert recovered.runtime().effective_context_window_tokens == (window or 256_000)
    assert recovered.context_window_tokens == window


def test_explicit_context_window_must_reserve_output_space() -> None:
    with pytest.raises(ValueError, match="reserve output space"):
        ResponsesConfig(
            base_url="https://example.test", api_key="test", context_window_tokens=16_384,
        )


class ResponsesRuntimeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "session"
        self.config = ResponsesConfig(base_url="https://example.test", api_key="unit-test-secret")

    def saved(self) -> Json:
        value: Json = json.loads((self.root / "session.json").read_text())
        return value

    async def test_compact_preserves_archive_and_supplies_authoritative_recovery_state(
        self,
    ) -> None:
        transport = ScriptedTransport(
            response(
                {"type": "reasoning", "encrypted_content": "opaque-state-not-source-evidence"},
                answer("Long evidence " * 150),
            ),
            response(
                {**answer("Temporary summary drafting"), "phase": "commentary"},
                {
                    **answer("Objective: optimize. Evidence: record_12. Next: compare."),
                    "phase": "final_answer",
                },
            ),
            response(answer("Continued")),
        )
        runtime = ResponsesRuntime(replace(self.config, compact_after_chars=1000), transport)
        first = await runtime.turn(
            self.root, system_prompt="Research", user_message="Begin",
            tools=[], handler=reject_call,
            recovery_state={"best_version": "v1", "active_jobs": ["old-job"]},
        )
        second = await runtime.turn(
            self.root,
            system_prompt="Research",
            user_message="Compare now",
            tools=[],
            handler=reject_call,
            recovery_state={"best_version": "v2", "active_jobs": ["j1"]},
        )
        self.assertEqual(first.session_id, second.session_id)
        state = self.saved()
        self.assertEqual(len(state["compactions"]), 1)
        self.assertIn("Long evidence " * 150, json.dumps(state["history"]))
        self.assertNotIn("Long evidence " * 150, json.dumps(transport.payloads[-1]["input"]))
        self.assertIn("v2", transport.payloads[-1]["input"][0]["content"])
        self.assertEqual(transport.payloads[-1]["input"][0]["role"], "assistant")
        self.assertEqual(
            [
                item["content"]
                for item in transport.payloads[-1]["input"]
                if item.get("role") == "user"
            ][-1],
            "Compare now",
        )
        self.assertNotIn("tools", transport.payloads[1])
        compact_payload = transport.payloads[1]
        archive = json.loads(compact_payload["input"][0]["content"])
        self.assertEqual(len(compact_payload["input"]), 1)
        self.assertEqual(archive["conversation"][0]["content"], "Begin")
        self.assertNotIn("Research", compact_payload["instructions"])
        self.assertEqual(archive["authoritative_state"]["best_version"], "v2")
        self.assertEqual(archive["active_instruction"]["text"], "Compare now")
        self.assertFalse(any(
            item.get("role") == "developer" and item.get("content", "").startswith(STATE_PREFIX)
            for item in archive["conversation"]
        ))
        self.assertIn("Long evidence " * 150, json.dumps(archive["conversation"]))
        self.assertTrue(any(
            item.get("role") == "developer" and item.get("content", "").startswith(STATE_PREFIX)
            for item in state["history"]
        ))
        self.assertNotIn("opaque-state-not-source-evidence", json.dumps(compact_payload))
        self.assertIn("opaque-state-not-source-evidence", json.dumps(state["history"]))
        self.assertNotIn("Temporary summary drafting", state["compactions"][0]["summary"])


CONFIG = ResponsesConfig(base_url="https://example.test", api_key="test", compact_after_chars=0)
TOOLS = [{"type": "function", "name": "read"}]


def test_progress_arriving_during_compaction_precedes_next_action(tmp_path: Path) -> None:
    async def scenario() -> None:
        queued: list[Json] = []
        acknowledged: list[str] = []
        update = {
            "message_id": "component-ready",
            "sender": "research_component",
            "recipient": "rsi",
            "text": "组件已通过，请继续整合",
        }

        class Transport(ScriptedTransport):
            async def request(
                self, config: ResponsesConfig, payload: Json, event_sink: EventSink
            ) -> Json:
                if len(self.payloads) == 1:
                    queued.append(update)
                return await super().request(config, payload, event_sink)

        transport = Transport(
            response(call("read", "evidence")),
            response(answer("旧材料已经核对，组件尚未就绪。")),
            response(answer("现在整合已就绪组件。")),
        )

        async def execute(item: ToolCall) -> ToolResult:
            return ToolResult("Archived evidence " * 150)

        async def sink(event: Json) -> None:
            if event["type"] == "message_injected":
                acknowledged.append(event["message_id"])
                queued.clear()

        config = replace(CONFIG, compact_after_chars=1000)
        runtime = ResponsesRuntime(config, transport)
        await runtime.turn(
            tmp_path,
            system_prompt="协调研究",
            user_message="继续当前阶段",
            message_id="stage",
            tools=TOOLS,
            handler=execute,
            event_sink=sink,
            incoming_messages=lambda: list(queued),
        )
        assert len(transport.payloads) == 3
        context = transport.payloads[-1]["input"]
        assert context[0]["role"] == "assistant" and "尚未就绪" in context[0]["content"]
        assert json.loads(context[1]["content"]) == update
        assert acknowledged == ["component-ready"]
        # Replaying a completed delivery neither requests a model nor re-acknowledges it.
        await ResponsesRuntime(config, transport).turn(
            tmp_path,
            system_prompt="协调研究",
            user_message=None,
            message_id="stage",
            tools=TOOLS,
            handler=execute,
            event_sink=sink,
            incoming_messages=lambda: list(queued),
        )
        assert len(transport.payloads) == 3 and acknowledged == ["component-ready"]

    asyncio.run(scenario())


def test_turns_append_state_and_budget_without_rewriting_the_prefix(tmp_path: Path) -> None:
    async def scenario() -> None:
        transport = ScriptedTransport(
            response(call("read", "first")),
            response(answer("Evidence one")),
            response(call("read", "second")),
            response(answer("Evidence two")),
        )
        runtime = ResponsesRuntime(CONFIG, transport)

        async def execute(item: ToolCall) -> ToolResult:
            return ToolResult({"observation": item.call_id})

        for index in (1, 2):
            await runtime.turn(
                tmp_path,
                system_prompt="Research the requested product",
                user_message=f"Stage {index}",
                message_id=f"stage-{index}",
                tools=TOOLS,
                handler=execute,
                recovery_state={"current_stage": index},
            )
        payloads = transport.payloads
        assert len(payloads) == 4
        assert len({p["instructions"] for p in payloads}) == 1
        for before, after in pairwise(payloads):
            assert after["input"][: len(before["input"])] == before["input"]
        snapshots = [
            json.loads(item["content"][len(STATE_PREFIX) :])
            for item in payloads[-1]["input"]
            if item.get("role") == "developer" and item.get("content", "").startswith(STATE_PREFIX)
        ]
        assert [s["authoritative_state"]["current_stage"] for s in snapshots] == [1, 2]
        assert all(set(snapshot) == {"authoritative_state"} for snapshot in snapshots)
        assert json.dumps(payloads[-1]["input"]).count("Stage 2") == 1
        assert "剩余 31 次" in payloads[-1]["input"][-1]["content"]

    asyncio.run(scenario())


def test_recovery_inserts_new_state_after_pending_tool_output(tmp_path: Path) -> None:
    async def scenario() -> None:
        transport = ScriptedTransport(response(call("read", "pending")), response(answer("Done")))
        runtime = ResponsesRuntime(CONFIG, transport)
        attempts = []

        async def execute(item: ToolCall) -> ToolResult:
            attempts.append(item.recovered)
            if not item.recovered:
                raise asyncio.CancelledError
            return ToolResult({"reconciled": True})

        with pytest.raises(asyncio.CancelledError):
            await runtime.turn(
                tmp_path,
                system_prompt="Research",
                user_message="Continue this stage",
                message_id="stage",
                tools=TOOLS,
                handler=execute,
                recovery_state={"best": "before"},
            )
        result = await runtime.turn(
            tmp_path,
            system_prompt="Research",
            user_message=None,
            message_id="stage",
            tools=TOOLS,
            handler=execute,
            recovery_state={"best": "after"},
        )
        assert result.stop_reason == "completed" and attempts == [False, True]
        before, after = transport.payloads
        assert before["instructions"] == after["instructions"]
        assert after["input"][: len(before["input"])] == before["input"]
        context = after["input"]
        output = next(
            i for i, item in enumerate(context) if item.get("type") == "function_call_output"
        )
        update = next(
            i
            for i, item in enumerate(context)
            if item.get("role") == "developer" and '"best": "after"' in item["content"]
        )
        assert output < update
        assert len([item for item in context if item.get("role") == "user"]) == 1
        assert json.dumps(context).count("Continue this stage") == 1

    asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
