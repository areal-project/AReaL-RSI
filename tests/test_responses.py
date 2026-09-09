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
        self.assertEqual(transport.payloads[-1]["input"][0]["role"], "assistant")
        self.assertEqual(transport.payloads[-1]["input"][-1]["content"], "Compare now")
        self.assertNotIn("tools", transport.payloads[1])
        compact_payload = transport.payloads[1]
        archive = json.loads(compact_payload["input"][0]["content"])
        self.assertEqual(len(compact_payload["input"]), 1)
        self.assertEqual(archive["conversation"][0]["content"], "Begin")
        self.assertNotIn("Research", compact_payload["instructions"])
        self.assertEqual(archive["authoritative_state"]["best_version"], "v2")
        self.assertNotIn("opaque-state-not-source-evidence", json.dumps(compact_payload))
        self.assertIn("opaque-state-not-source-evidence", json.dumps(state["history"]))
        self.assertNotIn("Temporary summary drafting", state["compactions"][0]["summary"])

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

    async def test_long_turn_compacts_after_complete_tool_batch_without_losing_pairs(self) -> None:
        transport = ScriptedTransport(
            response(call("read", "a"), call("read", "b")),
            response(answer("Evidence a and b support the original task; next submit results.")),
            response(answer("Completed the original task")),
        )
        effects: list[str] = []

        async def handler(item: ToolCall) -> ToolResult:
            effects.append(item.call_id)
            return ToolResult(item.call_id + ": " + "Recorded observation. " * 150)

        result = await ResponsesRuntime(
            replace(self.config, compact_after_chars=1000), transport
        ).turn(
            self.root,
            system_prompt="Research",
            user_message="Original assignment",
            tools=[tool("read")],
            handler=handler,
            message_id="assignment",
        )
        self.assertEqual(result.text, "Completed the original task")
        self.assertEqual(effects, ["a", "b"])
        compact_input = json.loads(transport.payloads[1]["input"][0]["content"])["conversation"]
        functions = [
            item["call_id"] for item in compact_input if item.get("type") == "function_call"
        ]
        outputs = [
            item["call_id"] for item in compact_input if item.get("type") == "function_call_output"
        ]
        self.assertEqual(functions, ["a", "b"])
        self.assertEqual(outputs, functions)
        self.assertNotIn("tools", transport.payloads[1])
        self.assertEqual(
            [item["content"] for item in compact_input if item.get("role") == "user"],
            ["Original assignment"],
        )
        self.assertNotIn("Recorded observation.", json.dumps(transport.payloads[2]["input"]))
        saved = self.saved()
        self.assertEqual(len(saved["turns"]), 1)
        self.assertEqual(len(saved["turns"][0]["requests"]), 3)
        self.assertEqual(saved["turns"][0]["message_id"], "assignment")
        self.assertEqual(saved["usage"]["total_tokens"], 75)
        self.assertTrue(saved["compactions"][0]["within_turn"])
        self.assertEqual(
            saved["compactions"][0]["active_instruction"],
            {
                "message_id": "assignment",
                "text": "Original assignment",
                "input_already_appended": True,
            },
        )
        self.assertIn("Original assignment", transport.payloads[2]["instructions"])
        self.assertEqual(transport.payloads[2]["input"][0]["role"], "assistant")
        self.assertIn("Recorded observation.", json.dumps(saved["history"]))
        self.assertTrue(all(record["status"] == "completed" for record in saved["calls"].values()))

    async def test_recovery_keeps_real_instruction_independent_of_summary(self) -> None:
        for message in ("封装已获授权，请继续接入准备", "请只整理摘要，暂时不要继续检查"):
            with self.subTest(message=message):
                directory = self.root / str(len(message))
                wrong_summary = "当前用户要求仅整理摘要，不继续检查、编码或研究。"
                transport = ScriptedTransport(
                    response(call("read", "large")),
                    response(answer(wrong_summary)),
                    response(answer("任务状态已核对")),
                )

                async def handler(_: ToolCall) -> ToolResult:
                    return ToolResult("已检查环境。" * 250)

                await ResponsesRuntime(
                    replace(self.config, compact_after_chars=1000), transport
                ).turn(
                    directory,
                    system_prompt="按用户任务准备研究",
                    user_message=message,
                    message_id="source-message",
                    tools=[tool("read")],
                    handler=handler,
                )
                continued = transport.payloads[-1]
                checkpoint = json.loads(continued["input"][0]["content"].split("\n", 1)[1])
                self.assertEqual(
                    checkpoint["summary"], wrong_summary
                )  # Audit keeps the actual output.
                self.assertEqual(checkpoint["active_instruction"]["text"], message)
                self.assertEqual(checkpoint["active_instruction"]["message_id"], "source-message")
                self.assertEqual(continued["input"][0]["role"], "assistant")
                self.assertIn(message, continued["instructions"])
                self.assertIn("不会暂停当前任务，也不需要用户解除", continued["instructions"])

    async def test_in_turn_compact_recovery_reuses_response_and_committed_checkpoint(self) -> None:
        for interrupt_at in ("response_usage", "session_compacted"):
            with self.subTest(interrupt_at=interrupt_at):
                session = self.root / interrupt_at
                transport = ScriptedTransport(
                    response(call("read", "a")),
                    response(answer("Saved evidence; submit report.")),
                    response(answer("done")),
                )

                async def handler(_: ToolCall) -> ToolResult:
                    return ToolResult("Long observation " * 150)

                async def interrupt(
                    event: Json, *, directory: Path = session, expected_event: str = interrupt_at
                ) -> None:
                    saved = json.loads((directory / "session.json").read_text())
                    latest = saved["turns"][-1]["requests"][-1]
                    if event["type"] == expected_event and latest["kind"] == "compact":
                        raise asyncio.CancelledError

                # The checkpoint itself exceeds this tiny test threshold, so
                # recovery must recognize the committed boundary, not recompact it.
                config = replace(self.config, compact_after_chars=200)
                kwargs: Json = {
                    "system_prompt": "Research",
                    "tools": [tool("read")],
                    "handler": handler,
                }
                with self.assertRaises(asyncio.CancelledError):
                    await ResponsesRuntime(config, transport).turn(
                        session, user_message="Go", event_sink=interrupt, **kwargs
                    )
                self.assertEqual(len(transport.payloads), 2)
                result = await ResponsesRuntime(config, transport).turn(
                    session, user_message=None, **kwargs
                )
                self.assertEqual(result.text, "done")
                self.assertEqual(len(transport.payloads), 3)
                saved = json.loads((session / "session.json").read_text())
                self.assertEqual(len(saved["compactions"]), 1)
                self.assertEqual(len(saved["turns"]), 1)
                self.assertEqual(saved["usage"]["total_tokens"], 75)

    async def test_in_turn_compact_does_not_split_interrupted_tool_batch(self) -> None:
        transport = ScriptedTransport(
            response(call("read", "a"), call("read", "b")),
            response(answer("Both tool results are saved")),
            response(answer("done")),
        )
        effects: list[str] = []

        async def handler(item: ToolCall) -> ToolResult:
            if item.call_id == "b" and not item.recovered:
                raise asyncio.CancelledError
            effects.append(item.call_id)
            return ToolResult("Observation " * 150)

        config = replace(self.config, compact_after_chars=1000)
        kwargs: Json = {"system_prompt": "Research", "tools": [tool("read")], "handler": handler}
        with self.assertRaises(asyncio.CancelledError):
            await ResponsesRuntime(config, transport).turn(self.root, user_message="Go", **kwargs)
        self.assertEqual(len(transport.payloads), 1)
        await ResponsesRuntime(config, transport).turn(self.root, user_message=None, **kwargs)
        self.assertEqual(effects, ["a", "b"])
        compact_input = json.loads(transport.payloads[1]["input"][0]["content"])["conversation"]
        self.assertEqual(
            [
                item["call_id"]
                for item in compact_input
                if item.get("type") == "function_call_output"
            ],
            ["a", "b"],
        )

    async def test_in_turn_compact_needs_budget_for_checkpoint_and_continuation(self) -> None:
        transport = ScriptedTransport(
            response(call("read", "a")),
            response(answer("Saved evidence")),
            response(answer("done")),
        )

        async def handler(_: ToolCall) -> ToolResult:
            return ToolResult("Observation " * 150)

        config = replace(self.config, compact_after_chars=1000, max_model_calls_per_turn=2)
        kwargs: Json = {"system_prompt": "Research", "tools": [tool("read")], "handler": handler}
        result = await ResponsesRuntime(config, transport).turn(
            self.root, user_message="Go", **kwargs
        )
        self.assertEqual(result.stop_reason, "budget_exhausted")
        self.assertEqual(len(transport.payloads), 1)
        await ResponsesRuntime(config, transport).turn(self.root, user_message=None, **kwargs)
        self.assertEqual(len(transport.payloads), 1)
        result = await ResponsesRuntime(config, transport).turn(
            self.root, user_message=None, retry_id="explicit_review", **kwargs
        )
        self.assertEqual(result.text, "done")
        self.assertEqual(len(transport.payloads), 3)
        self.assertEqual(self.saved()["turns"][0]["request_limit"], 3)

    async def test_checkpoint_deep_copies_nested_live_recovery_state(self) -> None:
        recovery: Json = {
            "plan": "Initial plan",
            "directions": [{"direction": "Layout", "state": "waiting"}],
            "budget": {"model_calls": 2},
        }
        original = copy.deepcopy(recovery)
        transport = ScriptedTransport(
            response(call("read", "a")),
            response(answer("Saved evidence")),
            response(answer("done")),
        )

        async def handler(_: ToolCall) -> ToolResult:
            return ToolResult("Long observation " * 150)

        async def update_after_checkpoint(event: Json) -> None:
            if event["type"] == "session_compacted":
                recovery["plan"] = "Updated plan"
                recovery["directions"][0]["state"] = "stopped"
                recovery["budget"]["model_calls"] = 3

        await ResponsesRuntime(replace(self.config, compact_after_chars=1000), transport).turn(
            self.root,
            system_prompt="Research",
            user_message="Go",
            tools=[tool("read")],
            handler=handler,
            recovery_state=recovery,
            event_sink=update_after_checkpoint,
        )
        self.assertEqual(self.saved()["compactions"][0]["authoritative_state"], original)
        self.assertEqual(recovery["directions"][0]["state"], "stopped")

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
        # A new runtime configuration must not silently enlarge this saved turn.
        await ResponsesRuntime(self.config, transport).turn(self.root, user_message=None, **kwargs)
        self.assertEqual(len(transport.payloads), 1)
        resumed = await ResponsesRuntime(config, transport).turn(
            self.root, user_message=None, retry_id="review_1", **kwargs
        )
        self.assertEqual(resumed.text, "done")

    async def test_explicit_retry_grant_is_finite_and_idempotent_without_tool_replay(self) -> None:
        transport = ScriptedTransport(
            response(call("write", "a")),
            response(call("write", "b")),
            response(answer("done")),
        )
        effects: list[str] = []

        async def handler(item: ToolCall) -> ToolResult:
            effects.append(item.call_id)
            return ToolResult("Saved")

        config = replace(self.config, max_model_calls_per_turn=1)
        kwargs: Json = {"system_prompt": "Research", "tools": [tool("write")], "handler": handler}
        await ResponsesRuntime(config, transport).turn(self.root, user_message="Go", **kwargs)
        result = await ResponsesRuntime(config, transport).turn(
            self.root, user_message=None, retry_id="review_1", **kwargs
        )
        self.assertEqual(result.stop_reason, "budget_exhausted")
        self.assertEqual(self.saved()["turns"][0]["request_limit"], 2)
        # A process restart replays the persisted retry ID, never creates a grant.
        await ResponsesRuntime(config, transport).turn(
            self.root, user_message=None, retry_id="review_1", **kwargs
        )
        self.assertEqual(len(transport.payloads), 2)
        result = await ResponsesRuntime(config, transport).turn(
            self.root, user_message=None, retry_id="review_2", **kwargs
        )
        self.assertEqual(result.text, "done")
        self.assertEqual(effects, ["a", "b"])
        self.assertEqual(len(self.saved()["turns"]), 1)
        self.assertEqual(self.saved()["usage"]["total_tokens"], 75)

    async def test_retry_grant_survives_cancellation_before_the_next_model_request(self) -> None:
        transport = ScriptedTransport(response(call("read", "a")), response(call("read", "b")))
        config = replace(self.config, max_model_calls_per_turn=1)

        async def handler(_: ToolCall) -> ToolResult:
            return ToolResult("Read")

        async def cancel_after_grant(event: Json) -> None:
            if event["type"] == "turn_retry_granted":
                raise asyncio.CancelledError

        kwargs: Json = {"system_prompt": "Research", "tools": [tool("read")], "handler": handler}
        await ResponsesRuntime(config, transport).turn(self.root, user_message="Go", **kwargs)
        with self.assertRaises(asyncio.CancelledError):
            await ResponsesRuntime(config, transport).turn(
                self.root,
                user_message=None,
                retry_id="review",
                event_sink=cancel_after_grant,
                **kwargs,
            )
        self.assertEqual(len(transport.payloads), 1)
        result = await ResponsesRuntime(config, transport).turn(
            self.root, user_message=None, retry_id="review", **kwargs
        )
        self.assertEqual(result.stop_reason, "budget_exhausted")
        self.assertEqual(self.saved()["turns"][0]["request_limit"], 2)
        self.assertEqual(len(transport.payloads), 2)

    async def test_request_prompt_counts_down_and_reserves_a_clean_handoff(self) -> None:
        transport = ScriptedTransport(*(response(call("read", str(i))) for i in range(5)))

        async def handler(_: ToolCall) -> ToolResult:
            return ToolResult("Read")

        result = await ResponsesRuntime(
            replace(self.config, max_model_calls_per_turn=5), transport
        ).turn(
            self.root,
            system_prompt="Research",
            user_message="Go",
            tools=[tool("read")],
            handler=handler,
        )
        self.assertEqual(result.stop_reason, "budget_exhausted")
        for index, payload in enumerate(transport.payloads):
            self.assertIn(f"剩余 {5 - index} 次模型调用", payload["instructions"])
            self.assertEqual(payload["model"], "gpt-6-astra")
            self.assertEqual(payload["reasoning"], {"effort": "xhigh"})
        self.assertNotIn("额度即将耗尽", transport.payloads[0]["instructions"])
        self.assertIn("按角色使用报告、明确等待", transport.payloads[-1]["instructions"])

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


def test_unlimited_turn_receives_steering_and_recovers_injection_ack(tmp_path: Path) -> None:
    async def scenario() -> None:
        transport = ScriptedTransport(
            *(response(call("read", str(i))) for i in range(34)), response(answer("完成"))
        )
        config = ResponsesConfig(
            base_url="https://example.test",
            api_key="test",
            max_model_calls_per_turn=None,
            compact_after_chars=0,
        )
        queue: list[Json] = []
        effects: list[str] = []
        crashed = False

        async def handler(item: ToolCall) -> ToolResult:
            effects.append(item.call_id)
            if item.call_id == "0":
                queue.append({"message_id": "steering", "sender": "user", "text": "现在开始"})
            return ToolResult("read completed")

        async def events(event: Json) -> None:
            nonlocal crashed
            if event["type"] == "message_injected":
                if not crashed:
                    crashed = True
                    raise RuntimeError("crash before coordinator acknowledgement")
                queue.clear()

        runtime = ResponsesRuntime(config, transport)
        kwargs: Any = dict(
            system_prompt="研究",
            tools=[tool("read")],
            handler=handler,
            message_id="original",
            event_sink=events,
            incoming_messages=lambda: queue,
        )
        try:
            await runtime.turn(tmp_path, user_message="读取环境", **kwargs)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Expected acknowledgement crash")
        result = await runtime.turn(tmp_path, user_message=None, **kwargs)
        assert result.stop_reason == "completed" and len(transport.payloads) == 35
        assert len(effects) == len(set(effects)) == 34
        saved = json.loads((tmp_path / "session.json").read_text())
        assert saved["turns"][0]["request_limit"] is None
        assert len(saved["turns"][0]["followups"]) == 1 and not queue
        injected = [
            x for x in saved["history"] if x.get("role") == "user" and "steering" in x["content"]
        ]
        assert len(injected) == 1
        assert "现在开始" in json.dumps(transport.payloads[1]["input"], ensure_ascii=False)

    asyncio.run(scenario())


def test_connection_and_read_failures_retry_beyond_old_limit(tmp_path: Path) -> None:
    async def scenario() -> None:
        for failure, fail_count, expected_attempts in (
            (httpx.ConnectError, 8, 9),
            (httpx.ConnectTimeout, 8, 9),
            (httpx.ReadError, 8, 9),
        ):
            attempts = 0

            async def handle(
                request: httpx.Request,
                fail_count: int = fail_count,
                failure: type[httpx.RequestError] = failure,
            ) -> httpx.Response:
                nonlocal attempts
                attempts += 1
                if attempts <= fail_count:
                    raise failure("private-credential", request=request)
                return httpx.Response(200, json=response(answer("ok")))

            async def emit(event: Json) -> None:
                assert "private-credential" not in json.dumps(event)

            transport = HttpResponsesTransport(httpx.MockTransport(handle))
            config = ResponsesConfig(
                base_url="https://example.test",
                api_key="private-credential",
                retry_initial_seconds=0.001,
                retry_max_seconds=0.002,
                max_model_calls_per_turn=1,
            )
            result = await ResponsesRuntime(config, transport).turn(
                tmp_path / failure.__name__,
                system_prompt="Continue",
                user_message="Go",
                tools=[],
                handler=reject_call,
                event_sink=emit,
            )
            assert result.text == "ok"
            assert attempts == expected_attempts

    asyncio.run(scenario())
