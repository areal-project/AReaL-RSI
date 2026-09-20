"""Retiring a native conversation also cancels its outstanding tool handlers."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from test_research_runtime_package import ResponsesModel

from areal_rsi.api.research import TaskBrief
from areal_rsi.coordination.actions import definition
from areal_rsi.coordination.config import ResearchConfig
from areal_rsi.modules.agent_runtime import ToolCall, ToolResult
from areal_rsi.modules.research_runtime import ResearchRuntime
from areal_rsi.modules.runtime_tools import invoke


def test_retire_one_codex_session_keeps_other_sessions_and_stops_old_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RSI_API_KEY", "fixture")
    model = ResponsesModel()

    async def scenario() -> None:
        runtime = ResearchRuntime(
            ResearchConfig(
                TaskBrief("test", "test"),
                backend="codex",
                base_url=model.url,
                model_settings_pinned=True,
            )
        )
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def handle(call: ToolCall) -> ToolResult:
            return ToolResult("current-state")

        async def blocked_handler(call: ToolCall) -> ToolResult:
            entered.set()
            try:
                await asyncio.Event().wait()
                return ToolResult("unexpected")
            finally:
                cancelled.set()

        old, other, fresh = [tmp_path / name for name in ("old", "other", "fresh")]
        pending: asyncio.Task[object] | None = None
        try:
            for session in (old, other):
                await asyncio.wait_for(
                    runtime.turn(
                        session,
                        system_prompt="Complete the task.",
                        user_message="Remember obsolete-context-marker"
                        if session == old
                        else "Work",
                        tools=[definition("get_state", "State")],
                        handler=handle,
                        workspace=tmp_path / (session.name + "-workspace"),
                    ),
                    30,
                )
            previous = runtime.conversations[old]
            survivor = runtime.conversations[other]
            previous.handler = blocked_handler
            bridge = json.loads((old / "runtime/request.json").read_text())["research_tools"]
            pending = asyncio.create_task(
                invoke(bridge, {"name": "get_state", "arguments": {}, "call_id": "blocked"})
            )
            await asyncio.wait_for(entered.wait(), 5)
            await asyncio.wait_for(runtime.close_session(old), 15)
            await asyncio.wait_for(cancelled.wait(), 5)
            assert old not in runtime.conversations
            assert previous.session.process.returncode is not None
            assert not previous.server.is_serving()
            assert not previous.calls or all(task.done() for task in previous.calls)
            assert survivor.session.process.returncode is None
            assert runtime.has_session(old)  # Historical data remains available.
            other_bridge = json.loads((other / "runtime/request.json").read_text())[
                "research_tools"
            ]
            assert (
                await invoke(
                    other_bridge, {"name": "get_state", "arguments": {}, "call_id": "alive"}
                )
                == "current-state"
            )
            await runtime.close_session(old)  # A repeated retirement is harmless.
            request_count = len(model.requests)
            await asyncio.wait_for(
                runtime.turn(
                    fresh,
                    system_prompt="Complete the task.",
                    user_message="The new hypothesis",
                    tools=[],
                    handler=handle,
                    workspace=tmp_path / "fresh-workspace",
                ),
                30,
            )
            assert "obsolete-context-marker" not in json.dumps(model.requests[request_count:])
            assert runtime.conversations[fresh].session is not previous.session
        finally:
            if pending:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            await runtime.close()

    try:
        asyncio.run(scenario())
    finally:
        model.close()
