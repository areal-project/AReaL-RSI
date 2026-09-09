"""Retry outages without losing conversations, tool effects or cancellation."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from areal_rsi.modules.responses import (
    HttpResponsesTransport,
    ResponsesConfig,
    ResponsesError,
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


async def reject(_: ToolCall) -> ToolResult:
    raise AssertionError("A failed response must never execute tools")


@pytest.mark.parametrize("failure", [408, 409, 429, 500, 502, 503, 504, "failed", "sse", "broken"])
def test_outage_recovers_in_same_turn_without_replaying_tools(tmp_path: Path, failure: Any) -> None:
    async def scenario() -> None:
        payloads, events, effects = [], [], []

        async def handle(request: httpx.Request) -> httpx.Response:
            payloads.append(json.loads(request.content))
            if len(payloads) == 1:
                return httpx.Response(200, json=completed(invocation("once")))
            if len(payloads) <= 9:
                if isinstance(failure, int):
                    return httpx.Response(failure, text="private-credential")
                packet = {
                    "status": "failed",
                    "output": [invocation("never")],
                    "error": {"code": "server_error", "message": "private-credential"},
                    "usage": {"total_tokens": 3},
                }
                if failure == "failed":
                    return httpx.Response(200, json=packet)
                event = (
                    {"type": "response.failed", "response": packet}
                    if failure == "sse"
                    else {"type": "response.output_item.done", "item": invocation("never")}
                )
                return httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    text="data: " + json.dumps(event) + "\n\n",
                )
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
        assert result.usage["total_tokens"] == (44 if failure in {"failed", "sse"} else 20)
        assert len(list((tmp_path / "responses").glob("*.attempt-*.json"))) == (
            8 if failure in {"failed", "sse"} else 0
        )

    asyncio.run(scenario())


def test_cancel_during_backoff_and_restart_preserves_deadline_and_request(tmp_path: Path) -> None:
    async def scenario() -> None:
        retrying = asyncio.Event()
        payloads: list[bytes] = []

        async def handle(request: httpx.Request) -> httpx.Response:
            payloads.append(request.content)
            return (
                httpx.Response(503) if len(payloads) == 1 else httpx.Response(200, json=completed())
            )

        async def emit(event: Json) -> None:
            if event["type"] == "model_retry":
                retrying.set()

        config = replace(
            CONFIG, max_model_calls_per_turn=1, retry_initial_seconds=0.15, retry_max_seconds=0.15
        )
        transport = HttpResponsesTransport(httpx.MockTransport(handle))
        kwargs: Json = dict(system_prompt="Work", message_id="task", tools=[], handler=reject)
        running = asyncio.create_task(
            ResponsesRuntime(config, transport).turn(
                tmp_path, user_message="Go", event_sink=emit, **kwargs
            )
        )
        await asyncio.wait_for(retrying.wait(), 1)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, 0.2)
        saved = json.loads((tmp_path / "session.json").read_text())
        request = saved["turns"][0]["requests"][0]
        assert request["status"] == "retrying" and len(payloads) == 1
        deadline = request["retry"]["next_retry_at"]
        result = await ResponsesRuntime(config, transport).turn(
            tmp_path, user_message=None, **kwargs
        )
        assert result.stop_reason == "completed" and time.time() >= deadline
        assert len(payloads) == 2 and payloads[0] == payloads[1]
        saved = json.loads((tmp_path / "session.json").read_text())
        assert len(saved["turns"]) == 1 and len(saved["turns"][0]["requests"]) == 1
        assert saved["turns"][0]["requests"][0]["attempt"] == 2

    asyncio.run(scenario())


def test_retry_after_is_honored_and_long_wait_is_cancellable(tmp_path: Path) -> None:
    async def scenario() -> None:
        async def handle(_: httpx.Request) -> httpx.Response:
            return httpx.Response(429, headers={"Retry-After": "120"})

        async def emit(event: Json) -> None:
            if event["type"] == "model_retry":
                assert event["delay_seconds"] == 120
                raise asyncio.CancelledError

        runtime = ResponsesRuntime(CONFIG, HttpResponsesTransport(httpx.MockTransport(handle)))
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(
                runtime.turn(
                    tmp_path,
                    system_prompt="Work",
                    user_message="Go",
                    tools=[],
                    handler=reject,
                    event_sink=emit,
                ),
                0.5,
            )

    asyncio.run(scenario())


def test_compact_outage_resumes_with_only_continuation_allowance_left(tmp_path: Path) -> None:
    async def scenario() -> None:
        count = 0

        async def handle(_: httpx.Request) -> httpx.Response:
            nonlocal count
            count += 1
            if count == 2:
                return httpx.Response(503)
            text = "saved fact " * 100 if count == 1 else "checkpoint"
            return httpx.Response(
                200,
                json=completed(
                    {
                        "type": "message",
                        "role": "assistant",
                        "phase": "final_answer",
                        "content": [{"type": "output_text", "text": text}],
                    }
                ),
            )

        async def interrupt(event: Json) -> None:
            if event["type"] == "model_retry":
                raise asyncio.CancelledError

        config = replace(CONFIG, compact_after_chars=500)
        transport = HttpResponsesTransport(httpx.MockTransport(handle))
        runtime = ResponsesRuntime(config, transport)
        kwargs: Json = dict(system_prompt="Work", tools=[], handler=reject)
        await runtime.turn(tmp_path, user_message="Start", message_id="first", **kwargs)
        with pytest.raises(asyncio.CancelledError):
            await runtime.turn(
                tmp_path,
                user_message="Continue",
                message_id="second",
                event_sink=interrupt,
                **kwargs,
            )
        result = await ResponsesRuntime(config, transport).turn(
            tmp_path, user_message=None, message_id="second", **kwargs
        )
        assert result.stop_reason == "completed" and count == 4
        saved = json.loads((tmp_path / "session.json").read_text())
        assert len(saved["compactions"]) == 1 and len(saved["turns"][-1]["requests"]) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [400, 401, 403, 404, "invalid_api_key", "incomplete"])
def test_permanent_errors_remain_actionable(tmp_path: Path, status: Any) -> None:
    async def scenario() -> None:
        count = 0

        async def handle(_: httpx.Request) -> httpx.Response:
            nonlocal count
            count += 1
            if isinstance(status, int):
                return httpx.Response(status, text="bad configuration private-credential")
            return httpx.Response(
                200,
                json={
                    "status": "incomplete" if status == "incomplete" else "failed",
                    "error": {"code": status},
                },
            )

        runtime = ResponsesRuntime(CONFIG, HttpResponsesTransport(httpx.MockTransport(handle)))
        with pytest.raises(ResponsesError) as error:
            await runtime.turn(
                tmp_path, system_prompt="Work", user_message="Go", tools=[], handler=reject
            )
        assert count == 1 and "private-credential" not in str(error.value)

    asyncio.run(scenario())


def test_terminal_sse_does_not_wait_for_socket_close() -> None:
    class OpenStream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield (
                "data: "
                + json.dumps({"type": "response.completed", "response": completed()})
                + "\n\n"
            ).encode()
            await asyncio.Event().wait()

        async def aclose(self) -> None:
            self.closed = True

    async def scenario() -> None:
        stream = OpenStream()

        async def handle(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

        async def emit(_: Json) -> None:
            pass

        result = await asyncio.wait_for(
            HttpResponsesTransport(httpx.MockTransport(handle)).request(CONFIG, {}, emit), 0.5
        )
        assert result["status"] == "completed" and stream.closed

    asyncio.run(scenario())
