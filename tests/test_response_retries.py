"""Retry outages without losing conversations, tool effects or cancellation."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx

from areal_rsi.modules.responses import (
    HttpResponsesTransport,
    ResponsesConfig,
    ResponsesRuntime,
    ToolCall,
    ToolResult,
)

Json = dict[str, Any]
CONFIG = ResponsesConfig(
    base_url="https://example.test",
    api_key="private-credential",
    max_model_calls_per_turn=2,
    retry_initial_seconds=0.001,
    retry_max_seconds=0.002,
)


def completed(*output: Json) -> Json:
    return {"status": "completed", "output": list(output), "usage": {"total_tokens": 10}}


def invocation(identifier: str) -> Json:
    return {"type": "function_call", "name": "record", "call_id": identifier, "arguments": "{}"}


def test_outage_recovers_in_same_turn_without_replaying_tools(tmp_path: Path) -> None:
    async def scenario() -> None:
        payloads, events, effects = [], [], []

        async def handle(request: httpx.Request) -> httpx.Response:
            payloads.append(json.loads(request.content))
            if len(payloads) == 1:
                return httpx.Response(200, json=completed(invocation("once")))
            if len(payloads) <= 9:
                return httpx.Response(503, text="private-credential")
            return httpx.Response(200, json=completed())

        async def execute(call: ToolCall) -> ToolResult:
            effects.append(call.call_id)
            return ToolResult("saved")

        async def emit(event: Json) -> None:
            events.append(event)

        runtime = ResponsesRuntime(CONFIG, HttpResponsesTransport(httpx.MockTransport(handle)))
        result = await runtime.turn(
            tmp_path,
            system_prompt="Work",
            user_message="Go",
            message_id="task",
            tools=[{"type": "function", "name": "record"}],
            handler=execute,
            event_sink=emit,
        )
        assert result.stop_reason == "completed" and effects == ["once"]
        assert len(payloads) == 10 and all(p == payloads[1] for p in payloads[2:])
        retries = [e for e in events if e["type"] == "model_retry"]
        assert len(retries) == 8 and "private-credential" not in json.dumps(retries)
        assert all(0 < e["delay_seconds"] <= CONFIG.retry_max_seconds for e in retries)
        saved = json.loads((tmp_path / "session.json").read_text())
        assert len(saved["turns"]) == 1 and len(saved["turns"][0]["requests"]) == 2
        assert len([x for x in saved["history"] if x.get("role") == "user"]) == 1
        assert result.usage["total_tokens"] == 20

    asyncio.run(scenario())
