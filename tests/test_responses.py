"""Real Responses wire shapes with deterministic transports and crash recovery."""

from __future__ import annotations

import asyncio
import copy
import json
import tempfile
import unittest
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx

from areal_rsi.modules.responses import (
    EventSink,
    HttpResponsesTransport,
    ResponsesConfig,
    ResponsesError,
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


def tool(name: str) -> Json:
    return {
        "type": "function",
        "name": name,
        "description": name,
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        "strict": True,
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


class ResponsesRuntimeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "session"
        self.config = ResponsesConfig(base_url="https://example.test", api_key="unit-test-secret")

    def saved(self) -> Json:
        value: Json = json.loads((self.root / "session.json").read_text())
        return value

    async def test_full_reasoning_and_phase_survive_wait_and_runtime_recreation(self) -> None:
        reasoning = {
            "type": "reasoning",
            "id": "rs_a",
            "summary": [],
            "encrypted_content": "opaque",
        }
        transport = ScriptedTransport(
            response(reasoning, answer("I will inspect it", phase="commentary"), call("read", "a")),
            response(answer("First result")),
            response(answer("Second result")),
        )
        events: list[Json] = []

        async def handler(item: ToolCall) -> ToolResult:
            self.assertEqual(item.call_id, "a")
            self.assertFalse(item.recovered)
            # The call is durably marked running BEFORE executing a side effect.
            self.assertEqual(self.saved()["calls"]["a"]["status"], "running")
            return ToolResult({"observation": "content"})

        async def event_sink(event: Json) -> None:
            events.append(event)

        first = await ResponsesRuntime(self.config, transport).turn(
            self.root,
            system_prompt="Research carefully",
            user_message="First assignment",
            tools=[tool("read")],
            handler=handler,
            message_id="assignment_1",
            event_sink=event_sink,
        )
        second = await ResponsesRuntime(self.config, transport).turn(
            self.root,
            system_prompt="Research carefully",
            user_message="Continue with the second assignment",
            tools=[tool("read")],
            handler=handler,
            message_id="assignment_2",
        )
        self.assertEqual(first.session_id, second.session_id)
        self.assertNotEqual(first.turn_id, second.turn_id)
        self.assertEqual(second.text, "Second result")
        self.assertIn(reasoning, transport.payloads[-1]["input"])
        self.assertIn(
            answer("I will inspect it", phase="commentary"), transport.payloads[-1]["input"]
        )
        self.assertEqual(transport.payloads[0]["reasoning"], {"effort": "xhigh"})
        self.assertEqual(transport.payloads[0]["model"], "gpt-6-astra")
        self.assertNotIn("previous_response_id", transport.payloads[-1])
        self.assertEqual(first.usage["total_tokens"], 50)
        self.assertEqual(first.usage["output_tokens_details"]["reasoning_tokens"], 6)
        self.assertEqual(self.saved()["usage"]["total_tokens"], 75)
        self.assertEqual(sum(event["type"] == "model_request" for event in events), 2)
        self.assertEqual(sum(event["type"] == "response_usage" for event in events), 2)
        self.assertNotIn("unit-test-secret", repr(self.config))
        self.assertNotIn("unit-test-secret", (self.root / "session.json").read_text())

    async def test_report_stop_prevents_remaining_calls_and_duplicate_wake_spends_nothing(
        self,
    ) -> None:
        transport = ScriptedTransport(
            response(call("report", "report_1"), call("dangerous", "not_executed")),
            response(answer("Resumed after explicit instruction")),
        )
        calls: list[str] = []

        async def handler(item: ToolCall) -> ToolResult:
            calls.append(item.name)
            return ToolResult({"report_id": "r1"}, stop=True)

        runtime = ResponsesRuntime(self.config, transport)
        kwargs: Json = {
            "system_prompt": "Report and wait",
            "user_message": "Do this assignment",
            "message_id": "one",
            "tools": [tool("report"), tool("dangerous")],
            "handler": handler,
        }
        first = await runtime.turn(self.root, **kwargs)
        self.assertEqual(first.stop_reason, "waiting")
        self.assertEqual(calls, ["report"])
        duplicate = await runtime.turn(self.root, **kwargs)
        self.assertEqual(first, duplicate)
        await asyncio.sleep(0)
        self.assertEqual(len(transport.payloads), 1)
        self.assertTrue(self.saved()["calls"]["not_executed"]["skipped"])
        await runtime.turn(self.root, **{**kwargs, "message_id": "two", "user_message": "Continue"})
        outputs = [item for item in transport.payloads[1]["input"] if "call_id" in item]
        self.assertEqual(len(outputs), 4)  # Every call has an output, even skipped calls.

    async def test_cancelled_effect_is_reconciled_by_same_call_id(self) -> None:
        transport = ScriptedTransport(
            response(call("submit_job", "job_call")), response(answer("OK"))
        )
        effects: dict[str, str] = {}
        seen: list[ToolCall] = []

        async def handler(item: ToolCall) -> ToolResult:
            seen.append(item)
            if item.call_id not in effects:
                effects[item.call_id] = "job_existing"
                raise asyncio.CancelledError  # Crash after the effect, before returning its ID.
            return ToolResult({"job_id": effects[item.call_id]})

        kwargs: Json = {
            "system_prompt": "Research",
            "tools": [tool("submit_job")],
            "handler": handler,
        }
        with self.assertRaises(asyncio.CancelledError):
            await ResponsesRuntime(self.config, transport).turn(
                self.root, user_message="Start a job", message_id="start", **kwargs
            )
        result = await ResponsesRuntime(self.config, transport).turn(
            self.root, user_message=None, **kwargs
        )
        self.assertEqual(result.text, "OK")
        self.assertEqual(len(effects), 1)
        self.assertEqual([item.recovered for item in seen], [False, True])
        self.assertEqual({item.call_id for item in seen}, {"job_call"})
        self.assertEqual(len(transport.payloads), 2)
        self.assertEqual(
            len([item for item in self.saved()["history"] if item.get("role") == "user"]), 1
        )

    async def test_cancel_after_response_before_consumption_does_not_request_again(self) -> None:
        transport = ScriptedTransport(response(call("report", "report_1")))
        fired = False

        async def interrupt(event: Json) -> None:
            nonlocal fired
            if event["type"] == "response_usage" and not fired:
                fired = True
                raise asyncio.CancelledError

        async def report(_: ToolCall) -> ToolResult:
            return ToolResult("saved", stop=True)

        kwargs: Json = {"system_prompt": "Research", "tools": [tool("report")], "handler": report}
        with self.assertRaises(asyncio.CancelledError):
            await ResponsesRuntime(self.config, transport).turn(
                self.root, user_message="Try", event_sink=interrupt, **kwargs
            )
        result = await ResponsesRuntime(self.config, transport).turn(
            self.root, user_message=None, **kwargs
        )
        self.assertEqual(result.stop_reason, "waiting")
        self.assertEqual(len(transport.payloads), 1)
        self.assertEqual(result.usage["total_tokens"], 25)

    async def test_completed_tool_not_repeated_after_later_tool_interrupts(self) -> None:
        transport = ScriptedTransport(
            response(call("first", "a"), call("second", "b")), response(answer("done"))
        )
        seen: list[str] = []

        async def handler(item: ToolCall) -> ToolResult:
            seen.append(item.call_id)
            if item.call_id == "b" and not item.recovered:
                raise asyncio.CancelledError
            return ToolResult("done")

        kwargs: Json = {
            "system_prompt": "Research",
            "tools": [tool("first"), tool("second")],
            "handler": handler,
        }
        with self.assertRaises(asyncio.CancelledError):
            await ResponsesRuntime(self.config, transport).turn(
                self.root, user_message="Try", **kwargs
            )
        await ResponsesRuntime(self.config, transport).turn(self.root, user_message=None, **kwargs)
        self.assertEqual(seen, ["a", "b", "b"])

    async def test_compact_preserves_archive_and_supplies_authoritative_recovery_state(
        self,
    ) -> None:
        transport = ScriptedTransport(
            response(answer("Long evidence " * 150)),
            response(answer("Objective: optimize. Evidence: record_12. Next: compare.")),
            response(answer("Continued")),
        )
        runtime = ResponsesRuntime(replace(self.config, compact_after_chars=1000), transport)
        first = await runtime.turn(
            self.root, system_prompt="Research", user_message="Begin", tools=[], handler=reject_call
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
        self.assertEqual(transport.payloads[-1]["input"][-1]["content"], "Compare now")
        self.assertNotIn("tools", transport.payloads[1])

    async def test_cancel_during_compact_keeps_new_user_instruction(self) -> None:
        transport = ScriptedTransport(
            response(answer("Old evidence " * 100)),
            response(answer("Compact evidence")),
            response(answer("done")),
        )
        runtime = ResponsesRuntime(replace(self.config, compact_after_chars=1000), transport)
        await runtime.turn(
            self.root, system_prompt="Research", user_message="One", tools=[], handler=reject_call
        )

        async def interrupt(event: Json) -> None:
            if event["type"] == "response_usage":
                raise asyncio.CancelledError

        with self.assertRaises(asyncio.CancelledError):
            await runtime.turn(
                self.root,
                system_prompt="Research",
                user_message="Must retain this instruction",
                tools=[],
                handler=reject_call,
                event_sink=interrupt,
            )
        await runtime.turn(
            self.root, system_prompt="Research", user_message=None, tools=[], handler=reject_call
        )
        self.assertEqual(len(transport.payloads), 3)
        self.assertEqual(
            transport.payloads[-1]["input"][-1]["content"], "Must retain this instruction"
        )

    async def test_call_budget_survives_resume_without_unbounded_retries(self) -> None:
        transport = ScriptedTransport(response(call("read", "a")), response(answer("done")))

        async def handler(_: ToolCall) -> ToolResult:
            return ToolResult("content")

        config = replace(self.config, max_model_calls_per_turn=1)
        kwargs: Json = {"system_prompt": "Research", "tools": [tool("read")], "handler": handler}
        result = await ResponsesRuntime(config, transport).turn(
            self.root, user_message="Go", **kwargs
        )
        self.assertEqual(result.stop_reason, "budget_exhausted")
        await ResponsesRuntime(config, transport).turn(self.root, user_message=None, **kwargs)
        self.assertEqual(len(transport.payloads), 1)
        resumed = await ResponsesRuntime(self.config, transport).turn(
            self.root, user_message=None, **kwargs
        )
        self.assertEqual(resumed.text, "done")

    async def test_incomplete_response_never_executes_partial_tools(self) -> None:
        transport = ScriptedTransport(response(call("write", "a"), status="incomplete"))
        with self.assertRaisesRegex(ResponsesError, "incomplete"):
            await ResponsesRuntime(self.config, transport).turn(
                self.root,
                system_prompt="Research",
                user_message="Go",
                tools=[tool("write")],
                handler=reject_call,
            )
        self.assertEqual(self.saved()["calls"], {})
        self.assertEqual(len(list((self.root / "responses").glob("*.json"))), 1)

    async def test_same_message_id_cannot_silently_discard_changed_instruction(self) -> None:
        transport = ScriptedTransport(response(answer("done")))
        runtime = ResponsesRuntime(self.config, transport)
        kwargs: Json = {
            "system_prompt": "Research",
            "tools": [],
            "handler": reject_call,
            "message_id": "id",
        }
        await runtime.turn(self.root, user_message="One", **kwargs)
        with self.assertRaisesRegex(ResponsesError, "different instruction"):
            await runtime.turn(self.root, user_message="Two", **kwargs)

    async def test_commentary_message_does_not_prematurely_complete_the_turn(self) -> None:
        transport = ScriptedTransport(
            response(answer("Checking the evidence", phase="commentary")),
            response(answer("The checked conclusion")),
        )
        result = await ResponsesRuntime(self.config, transport).turn(
            self.root, system_prompt="Research", user_message="Check", tools=[], handler=reject_call
        )
        self.assertEqual(result.text, "The checked conclusion")
        self.assertEqual(len(transport.payloads), 2)

    async def test_duplicate_call_ids_rejected_before_any_effect(self) -> None:
        transport = ScriptedTransport(response(call("write", "same"), call("write", "same")))
        with self.assertRaisesRegex(ResponsesError, "duplicate call_ids"):
            await ResponsesRuntime(self.config, transport).turn(
                self.root,
                system_prompt="Research",
                user_message="Go",
                tools=[tool("write")],
                handler=reject_call,
            )
        self.assertEqual(self.saved()["calls"], {})


class BlockingStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.started.set()
        yield b'event: response.created\ndata: {"type":"response.created"}\n\n'
        await asyncio.Event().wait()

    async def aclose(self) -> None:
        self.closed = True


class HttpResponsesTransportTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.config = ResponsesConfig(base_url="https://example.test/v1", api_key="secret-key")

    async def test_sse_protocol_and_usage(self) -> None:
        terminal = response(answer("hello"))
        received: list[httpx.Request] = []

        async def handle(request: httpx.Request) -> httpx.Response:
            received.append(request)
            body = (
                "event: response.output_text.delta\ndata: "
                + json.dumps({"type": "response.output_text.delta", "delta": "hello"})
                + "\n\n"
            )
            body += (
                "data: "
                + json.dumps({"type": "response.completed", "response": terminal})
                + "\n\ndata: [DONE]\n\n"
            )
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body)

        events: list[Json] = []

        async def emit(event: Json) -> None:
            events.append(event)

        result = await HttpResponsesTransport(httpx.MockTransport(handle)).request(
            self.config, {"model": "gpt-6-astra", "stream": True}, emit
        )
        self.assertEqual(result, terminal)
        self.assertEqual(str(received[0].url), "https://example.test/v1/responses")
        self.assertEqual(received[0].headers["authorization"], "Bearer secret-key")
        self.assertEqual(events[0]["delta"], "hello")

    async def test_http_error_does_not_retry_or_expose_credentials(self) -> None:
        count = 0

        async def handle(request: httpx.Request) -> httpx.Response:
            nonlocal count
            count += 1
            return httpx.Response(400, text="unsupported xhigh, secret-key")

        async def emit(_: Json) -> None:
            pass

        with self.assertRaises(ResponsesError) as caught:
            await HttpResponsesTransport(httpx.MockTransport(handle)).request(self.config, {}, emit)
        self.assertEqual(count, 1)
        self.assertIn("xhigh", str(caught.exception))
        self.assertNotIn("secret-key", str(caught.exception))

    async def test_cancellation_closes_inflight_socket(self) -> None:
        stream = BlockingStream()

        async def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

        async def emit(_: Json) -> None:
            pass

        task = asyncio.create_task(
            HttpResponsesTransport(httpx.MockTransport(handle)).request(self.config, {}, emit)
        )
        await asyncio.wait_for(stream.started.wait(), timeout=2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(stream.closed)


if __name__ == "__main__":
    unittest.main()
