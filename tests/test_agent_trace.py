"""Per-researcher public history, bounded paging and honest activity signals."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from runtime_fakes import ActionRuntime

from areal_rsi.api.control import public_tool_text
from areal_rsi.api.research import ResearchLimits, TaskBrief
from areal_rsi.infra.coordination.coordinator import Coordinator
from areal_rsi.infra.coordination.engine import ResearchEngine, ToolSet
from areal_rsi.infra.coordination.policies.legacy_passk import PassKActions, launch
from areal_rsi.infra.coordination.views import agent_trace
from areal_rsi.infra.research import ResearchSystem
from areal_rsi.infra.storage.files import encode


def test_agent_trace_is_scoped_paginated_and_read_only(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Coordinator.create(tmp_path, TaskBrief("task", "task", ResearchLimits(max_agents=2)))
        c.state.mode = "legacy_passk"
        c.start()
        await launch(c, PassKActions(c))
        first, second = [a.agent_id for a in c.store.run().research_agents]
        for index in range(7):
            c.store.event("assistant_output", agent_id=first, text=f"first-{index}")
            c.store.event("assistant_output", agent_id=second, text=f"second-{index}")
        c.store.event("tool_completed", agent_id=first, name="exec_command", call_id="call",
                      arguments='curl -H "Authorization: Bearer credential123" example.com',
                      result={"api_key": "private-key", "output": "checked"}, status="failed")
        c.store.event("goal_updated", agent_id=first, goal={"status": "completed"})
        c.store.event("agent_suspended", agent_id=first, reason="worker_interrupted")
        c.store.event("tool_started", agent_id="interaction", name="exec_command",
                      call_id="interaction-call",
                      arguments={"cmd": "cat visible.txt; echo token=private-secret"},
                      status="running")
        message = c.enqueue("user", first, "Only the first researcher", key="direct")
        before = {p: p.read_bytes() for p in c.store.root.rglob("*.json")}
        app = ResearchSystem(tmp_path)
        try:
            page = encode(agent_trace(c, first, limit=3))
            all_entries = list(page["entries"])
            while page["next_before"] is not None:
                page = encode(agent_trace(c, first, before=page["next_before"], limit=3))
                all_entries = page["entries"] + all_entries
            sequences = [e["sequence"] for e in all_entries]
            assert sequences == sorted(set(sequences))
            texts = [e["text"] for e in all_entries]
            assert all(f"first-{index}" in texts for index in range(7))
            assert not any("second-" in text for text in texts)
            assert "Only the first researcher" in texts
            tool = next(e for e in all_entries if e["call_id"] == "call")
            assert "credential123" not in tool["arguments"]
            assert "private-key" not in tool["result"] and "checked" in tool["result"]
            assert tool["status"] == "failed"
            assert any(e["kind"] == "agent_finished" for e in all_entries)
            interaction = encode(agent_trace(c, "interaction"))
            tool = next(e for e in interaction["entries"]
                        if e["call_id"] == "interaction-call")
            assert "visible.txt" in tool["arguments"]
            assert "private-secret" not in tool["arguments"]
            assert "first-0" not in str(interaction)
            peer = encode(agent_trace(c, second))
            assert "Only the first" not in str(peer)
            assert message not in str(peer)
            with pytest.raises(ValueError, match="不属于"):
                agent_trace(c, "research_foreign")
            with pytest.raises(ValueError):
                agent_trace(c, first, limit=10000)
            assert before == {p: p.read_bytes() for p in c.store.root.rglob("*.json")}
        finally:
            await app.close()
    asyncio.run(scenario())


def test_activity_tracks_concurrent_tools_and_api_wait_without_usage_heartbeats(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        c = Coordinator.create(tmp_path, TaskBrief("task", "task"))
        actions = PassKActions(c)
        engine = ResearchEngine(c, ActionRuntime(),
                                lambda actor: ToolSet([], actions.handler(actor)))
        try:
            await engine._event("rsi", {"type": "tool_started", "name": "slow", "call_id": "a"})
            await engine._event("rsi", {"type": "tool_started", "name": "fast", "call_id": "b"})
            await engine._event("rsi", {"type": "tool_completed", "name": "fast", "call_id": "b"})
            activity = c.state.agent_activity["rsi"]
            assert list(activity.active_tools) == ["a"] and activity.tool == "slow"
            previous = activity.last_progress_at
            await engine._event("rsi", {"type": "runtime_usage", "event_id": "usage",
                                        "usage": {"total_tokens": 1}})
            assert activity.last_progress_at == previous
            await engine._event("rsi", {"type": "api_availability", "available": False,
                                        "timestamp": 100})
            assert activity.api_available is False and activity.active_tools
            await engine._event("rsi", {"type": "tool_completed", "name": "slow", "call_id": "a",
                                        "status": "failed"})
            assert not activity.active_tools
            record = next(e for e in c.store.events() if e.kind == "tool_completed"
                          and e.data.get("call_id") == "a")
            assert record.data["started_at"]
            assert any(e.kind == "api_availability" for e in c.store.events())
        finally:
            await engine.close()
    asyncio.run(scenario())


def test_public_tool_excerpts_redact_and_bound_text() -> None:
    value = public_tool_text({"env": {"TOKEN": "private"}, "command": "cat output.txt",
                              "output": 'API_KEY="secret-value"\nAuthorization: Bearer abcdefghi'})
    assert "private" not in value and "secret-value" not in value and "abcdefghi" not in value
    assert "output.txt" in value
    assert len(public_tool_text("x" * 10000, limit=100)) < 150
    command = public_tool_text({"command": "printf 'api_key=hidden-secret\\n'; cat proof.txt"})
    decoded = json.loads(command)
    assert "hidden-secret" not in decoded["command"] and "proof.txt" in decoded["command"]
    assert json.loads(public_tool_text(command)) == decoded


@pytest.mark.parametrize("agent_id", ["interaction", "rsi"])
def test_conversation_agent_trace_restores_public_output(tmp_path: Path, agent_id: str) -> None:
    async def scenario() -> None:
        c = Coordinator.create(tmp_path, TaskBrief("task", "task"))
        c.store.event("assistant_output", agent_id=agent_id, text="我先检查运行环境")
        c.store.event("assistant_output", agent_id="research_foreign", text="其他 Agent 的输出")
        c.store.event("tool_started", agent_id=agent_id, name="exec_command", call_id="check")
        app = ResearchSystem(tmp_path)
        try:
            latest = encode(agent_trace(c, agent_id, limit=1))
            assert latest["entries"][0]["name"] == "exec_command"
            earlier = encode(agent_trace(c, agent_id, before=latest["next_before"]))
            assert any(e["text"] == "我先检查运行环境" for e in earlier["entries"])
            assert "其他 Agent" not in str(earlier)
        finally:
            await app.close()
    asyncio.run(scenario())
