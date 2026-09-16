"""Actual Application admission, committed tools, steering and backend recovery."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from areal_agent_runtime import ARealAgentRuntime
from areal_agent_runtime.adapters.storage import SQLiteStateStore
from areal_agent_runtime.contracts import EventType, RunEvent, RunStatus, SteerRunCommand
from test_responses import ScriptedTransport, answer, call, response

from areal_rsi.modules.agent_runtime import AgentRuntimeError, ToolCall, ToolResult, TurnResult
from areal_rsi.modules.codex_transport import CodexGateway
from areal_rsi.modules.responses import ResponsesConfig, ResponsesRuntime
from areal_rsi.modules.runtime_research import ResearchRuntime

Json = dict[str, Any]
CONFIG = ResponsesConfig(base_url="https://example.test/v1", api_key="local-fixture")
TOOLS = [
    {
        "type": "function",
        "name": "report",
        "description": "Report the stage",
        "strict": True,
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }
]


async def committed(directory: Path) -> tuple[SQLiteStateStore, Json, list[RunEvent]]:
    path = next(directory.glob("areal-runtime/*/active.json"))
    binding = json.loads(path.read_text())
    state = SQLiteStateStore(path.parent / "state")
    return state, binding, list(await state.list_events(binding["run_id"]))


def test_application_denies_ungranted_tool_even_if_backend_requests_it(tmp_path: Path) -> None:
    class UntrustedEngine(ResponsesRuntime):
        async def turn(self, session_dir: Path, **kwargs: Any) -> TurnResult:
            result = await kwargs["handler"](ToolCall("not_granted", {}, "attempt"))
            assert "error" in result.output
            return TurnResult("session", "turn", "Denied", "completed", {})

    async def scenario() -> None:
        runtime = ResearchRuntime(UntrustedEngine(CONFIG))

        async def handler(tool: ToolCall) -> ToolResult:
            raise AssertionError("Unauthorized tool reached the product")

        try:
            await asyncio.wait_for(
                runtime.turn(
                    tmp_path,
                    system_prompt="Test",
                    user_message="Work",
                    tools=TOOLS,
                    handler=handler,
                ),
                3,
            )
            _, _, events = await committed(tmp_path)
            failures = [event for event in events if event.type == EventType.TOOL_FAILED]
            assert len(failures) == 1
            error = failures[0].data["error"]
            assert isinstance(error, dict) and error["code"] == "TOOL_NOT_AUTHORIZED"
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_standard_tool_commit_resume_and_replay(tmp_path: Path) -> None:
    async def scenario() -> None:
        transport = ScriptedTransport(
            response(call("report", "report-1")), response(answer("Next"))
        )
        runtime = ResearchRuntime(ResponsesRuntime(CONFIG, transport))
        calls: list[str] = []

        async def handler(tool: ToolCall) -> ToolResult:
            _, _, events = await committed(tmp_path)
            assert events[-1].type == EventType.TOOL_STARTED
            calls.append(tool.call_id)
            return ToolResult({"evidence": "retained"}, stop=True, reason="waiting")

        async def observe(event: Json) -> None:
            if event["type"] == "tool_completed":
                _, _, events = await committed(tmp_path)
                assert events[-1].type == EventType.TOOL_COMPLETED

        kwargs: Json = {
            "system_prompt": "Complete the research stage.",
            "tools": TOOLS,
            "handler": handler,
            "event_sink": observe,
        }
        first = await runtime.turn(tmp_path, user_message="Work", message_id="one", **kwargs)
        assert first.stop_reason == "waiting" and len(transport.payloads) == 1
        assert transport.payloads[0]["tools"] == TOOLS
        state, first_binding, events = await committed(tmp_path)
        snapshot = await state.get_run(first_binding["run_id"])
        assert snapshot and snapshot.status == RunStatus.COMPLETED
        assert [e.type for e in events].count(EventType.TOOL_COMPLETED) == 1
        assert calls == ["report-1"]
        await runtime.close()

        runtime = ResearchRuntime(ResponsesRuntime(CONFIG, transport))
        replay = await runtime.turn(tmp_path, user_message=None, message_id="one", **kwargs)
        _, replay_binding, _ = await committed(tmp_path)
        assert replay == first and replay_binding == first_binding
        assert len(transport.payloads) == 1 and calls == ["report-1"]
        continued = await runtime.turn(
            tmp_path, user_message="Continue", message_id="two", **kwargs
        )
        _, next_binding, _ = await committed(tmp_path)
        assert next_binding["session_id"] == first_binding["session_id"]
        assert next_binding["run_id"] != first_binding["run_id"]
        assert continued.session_id == first.session_id and continued.text == "Next"
        assert "retained" in json.dumps(transport.payloads[-1])
        await runtime.close()

    asyncio.run(scenario())


def test_steer_crosses_application_while_tool_is_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        entered, steered = asyncio.Event(), asyncio.Event()
        delivered: list[str] = []
        original = ARealAgentRuntime.steer_run

        async def steer(app: ARealAgentRuntime, command: SteerRunCommand) -> None:
            assert entered.is_set()
            await original(app, command)
            steered.set()

        monkeypatch.setattr(ARealAgentRuntime, "steer_run", steer)
        transport = ScriptedTransport(response(call("report", "one")), response(answer("Done")))
        runtime = ResearchRuntime(ResponsesRuntime(CONFIG, transport))

        async def handler(tool: ToolCall) -> ToolResult:
            entered.set()
            await asyncio.wait_for(steered.wait(), 3)
            return ToolResult({"ok": True})

        async def observe(event: Json) -> None:
            if event["type"] == "message_injected":
                delivered.append(event["message_id"])

        result = await asyncio.wait_for(
            runtime.turn(
                tmp_path,
                system_prompt="Work",
                user_message="Begin",
                tools=TOOLS,
                handler=handler,
                event_sink=observe,
                incoming_messages=lambda: (
                    [{"message_id": "update", "sender": "peer", "text": "new evidence"}]
                    if entered.is_set() and not delivered
                    else []
                ),
            ),
            5,
        )
        assert result.text == "Done" and delivered == ["update"]
        assert "new evidence" in json.dumps(transport.payloads[-1])
        await runtime.close()

    asyncio.run(scenario())


def test_pause_during_tool_leaves_recoverable_native_receipt(tmp_path: Path) -> None:
    async def scenario() -> None:
        entered = asyncio.Event()
        runtime = ResearchRuntime(
            ResponsesRuntime(CONFIG, ScriptedTransport(response(call("report", "same-call"))))
        )

        async def blocked(tool: ToolCall) -> ToolResult:
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("Cancelled tool resumed")

        task = asyncio.create_task(
            runtime.turn(
                tmp_path,
                system_prompt="Work",
                user_message="Begin",
                message_id="one",
                tools=TOOLS,
                handler=blocked,
            )
        )
        await asyncio.wait_for(entered.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        state, binding, _ = await committed(tmp_path)
        snapshot = await state.get_run(binding["run_id"])
        assert snapshot and snapshot.status == RunStatus.CANCELLED
        await runtime.close()

        async def recovered(tool: ToolCall) -> ToolResult:
            assert tool.call_id == "same-call" and tool.recovered
            return ToolResult({"existing_effect": True}, stop=True)

        runtime = ResearchRuntime(ResponsesRuntime(CONFIG, ScriptedTransport()))
        result = await runtime.turn(
            tmp_path,
            system_prompt="Work",
            user_message=None,
            message_id="one",
            tools=TOOLS,
            handler=recovered,
        )
        assert result.stop_reason == "waiting"
        await runtime.close()

    asyncio.run(scenario())


def test_framework_shutdown_recovers_the_same_run_from_sqlite(tmp_path: Path) -> None:
    async def scenario() -> None:
        entered = asyncio.Event()
        runtime = ResearchRuntime(
            ResponsesRuntime(CONFIG, ScriptedTransport(response(call("report", "same-call"))))
        )

        async def interrupted(tool: ToolCall) -> ToolResult:
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("Interrupted handler continued")

        task = asyncio.create_task(
            runtime.turn(
                tmp_path,
                system_prompt="Work",
                user_message="Begin",
                message_id="one",
                tools=TOOLS,
                handler=interrupted,
            )
        )
        await asyncio.wait_for(entered.wait(), 3)
        _, before, _ = await committed(tmp_path)
        # Framework shutdown differs from a user pause: the Run remains
        # recoverable and its native pending tool receipt stays on disk.
        await next(iter(runtime._applications)).shutdown()
        with pytest.raises(AgentRuntimeError, match="execution interrupted"):
            await task
        await runtime.close()

        async def recovered(tool: ToolCall) -> ToolResult:
            assert tool.call_id == "same-call" and tool.recovered
            return ToolResult({"reconciled": True}, stop=True)

        runtime = ResearchRuntime(ResponsesRuntime(CONFIG, ScriptedTransport()))
        try:
            result = await asyncio.wait_for(
                runtime.turn(
                    tmp_path,
                    system_prompt="Work",
                    user_message=None,
                    message_id="one",
                    tools=TOOLS,
                    handler=recovered,
                ),
                5,
            )
            state, after, events = await committed(tmp_path)
            assert result.stop_reason == "waiting" and before == after
            snapshot = await state.get_run(after["run_id"])
            assert snapshot and snapshot.status == RunStatus.COMPLETED
            assert sum(e.type == EventType.TOOL_COMPLETED for e in events) == 1
        finally:
            await runtime.close()

    asyncio.run(scenario())


def sse(output: Json, identifier: str) -> bytes:
    terminal = {**response(output), "id": identifier}
    events = [
        {"type": "response.created", "response": {"id": identifier}},
        {"type": "response.output_item.done", "output_index": 0, "item": output},
        {"type": "response.completed", "response": terminal},
    ]
    return "".join("data: " + json.dumps(e) + "\n\n" for e in events).encode()


def test_real_codex_application_tool_and_native_thread_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Uses the pinned real Codex binary; every model request stays in MockTransport."""

    async def scenario() -> None:
        requests: list[Json] = []
        original = CodexGateway.start

        async def respond(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            requests.append(payload)
            output = (
                {
                    "type": "custom_tool_call",
                    "id": "fc_report",
                    "call_id": "report-1",
                    "name": "exec",
                    "input": "text(await tools.rsi__report({}));",
                }
                if len(requests) == 1
                else answer("Continued")
            )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=sse(output, f"resp_{len(requests)}"),
            )

        async def start(gateway: CodexGateway) -> None:
            await gateway.http.aclose()
            gateway.http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
            await original(gateway)

        monkeypatch.setattr(CodexGateway, "start", start)
        runtime = ResearchRuntime.create("codex", CONFIG)
        calls: list[str] = []

        async def handler(tool: ToolCall) -> ToolResult:
            calls.append(tool.name)
            return ToolResult({"evidence": "preserved"}, stop=True, reason="waiting")

        kwargs: Json = {"system_prompt": "Research fixture", "tools": TOOLS, "handler": handler}
        try:
            first = await asyncio.wait_for(
                runtime.turn(tmp_path, user_message="Begin", message_id="one", **kwargs), 30
            )
            assert first.stop_reason == "waiting" and calls == ["report"]
            _, binding, events = await committed(tmp_path)
            assert sum(e.type == EventType.TOOL_COMPLETED for e in events) == 1
            second = await asyncio.wait_for(
                runtime.turn(tmp_path, user_message="Continue", message_id="two", **kwargs), 30
            )
            _, next_binding, _ = await committed(tmp_path)
            assert second.session_id == first.session_id and second.text == "Continued"
            assert binding["session_id"] == next_binding["session_id"]
            assert len(requests) == 2
            assert "Research fixture" in json.dumps(requests[0])
        finally:
            await runtime.close()

    asyncio.run(scenario())
