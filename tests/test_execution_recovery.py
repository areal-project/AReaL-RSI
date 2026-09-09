from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from areal_rsi.api.base import AgentState, RunState
from areal_rsi.api.research import (
    ResearchLimits,
    ResearchMessage,
    TaskBrief,
    VerificationContract,
    VerifiedResult,
)
from areal_rsi.base.storage import now
from areal_rsi.coordination.actions import ResearchActions
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.modules.responses import ToolCall


def test_infra_failure_cannot_end_research_but_user_can_stop(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Coordinator.create(
            tmp_path, TaskBrief("Research", "Continue", verification=VerificationContract("score"))
        )
        c.start()
        actions = ResearchActions(c)
        candidate = await c.store.snapshot(
            actions.workspace("rsi"),
            agent_id="operator",
            description="Candidate under verification",
        )
        c.store.record_verification(
            VerifiedResult(
                "job1",
                candidate.candidate_id,
                c.store.contract_digest(),
                now(),
                "infra_error",
                error="Pinned source unavailable",
            )
        )
        result = await actions.handler("rsi")(
            ToolCall("stop_run", {"reason": "Environment failed twice"}, "stop")
        )
        assert "ask_user" in result.output["error"]
        assert c.state["status"] == "running"
        question = await actions.handler("rsi")(
            ToolCall("ask_user", {"question": "请恢复运行环境"}, "ask")
        )
        assert question.stop and c.user_questions()
        assert c.actor("rsi").state == AgentState.WAITING
        c.stop("User explicitly stopped")
        assert c.state["status"] == "stopped"

    asyncio.run(scenario())


def test_user_reopen_preserves_reports_sessions_and_individually_stopped_directions(
    tmp_path: Path,
) -> None:
    c = Coordinator.create(
        tmp_path, TaskBrief("Research", "Continue", ResearchLimits(max_agents=2))
    )
    c.start()
    fields = {
        k: "Observed and recorded"
        for k in ["task_status", "work", "problems", "results", "interpretation", "next_steps"]
    }
    a = c.spawn(key="a", direction="A", instruction="A", completion_criteria="Report")
    b = c.spawn(key="b", direction="B", instruction="B", completion_criteria="Report")
    for agent in [a["agent_id"], b["agent_id"]]:
        c.begin_turn(agent)
        c.report(agent, key=agent, fields=fields)
        c.end_turn(agent)
    c.stop_agent(b["agent_id"], "No further work")
    before = c.actor(a["agent_id"])
    reports = {p.name: p.read_bytes() for p in (c.store.root / "reports").glob("*.json")}
    c.stop("Pause research externally")
    c.reopen()
    assert c.state["status"] == "running" and c.store.run().state == RunState.RUNNING
    assert c.store.run().finished_at is None
    assert c.actor(a["agent_id"]).session_id == before.session_id
    assert c.actor(a["agent_id"]).iterations == before.iterations
    assert c.actor(a["agent_id"]).state == AgentState.WAITING
    assert c.actor(a["agent_id"]).wait_reason == "rsi_reply"
    assert c.actor(b["agent_id"]).state == AgentState.STOPPED
    assert c.pending() == ["rsi"]
    assert {p.name: p.read_bytes() for p in (c.store.root / "reports").glob("*.json")} == reports
    messages = c.store.records("messages", ResearchMessage)
    assert any(m.sender == "user" and m.recipient == "rsi" and "恢复" in m.text for m in messages)
    with pytest.raises(ValueError):
        c.reopen()
