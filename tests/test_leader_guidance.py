"""Targeted sharing preserves independent attempts and the pass@k control."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from areal_agent_runtime.contracts.events import EventType, RunEvent
from areal_agent_runtime.contracts.product import RunSnapshot, RunStatus
from test_passk import setup

from areal_rsi.api.base import AgentState, RecordRef
from areal_rsi.coordination.collaboration import build_plan
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.coordination.leader_guidance import GuidedPassKActions
from areal_rsi.coordination.passk import PassKActions, PassKEngine
from areal_rsi.coordination.store import ResearchStore
from areal_rsi.modules.agent_runtime import ToolCall
from areal_rsi.modules.runtime_storage import JournalStateStore


def test_targeted_advice_preserves_running_attempts_and_read_only_grants(tmp_path: Path) -> None:
    async def scenario() -> None:
        c, baseline, agents = await setup(tmp_path)
        source, target, other = agents
        plan = build_plan("passk", ("leader_guidance",))
        actions = plan.actions(c)
        assert isinstance(actions, GuidedPassKActions) and plan.engine is PassKEngine
        assert build_plan("passk").actions is PassKActions
        assert "advise_researcher" not in {t["name"] for t in baseline.definitions("rsi")}
        assert not {"assign", "stop_researcher", "run_job", "share_evidence"} & {
            t["name"] for t in actions.definitions("rsi")
        }
        for agent in ["rsi", *agents]:
            c.begin_turn(agent)
        (actions.workspace(source) / "kernel.py").write_text("# original experiment\n")
        candidate = await c.store.snapshot(
            actions.workspace(source), agent_id=source, description="original experiment"
        )
        ref = c.store.record_reference(RecordRef("candidates/" + candidate.candidate_id))
        nodes = [c.actor(agent).current_iteration_id for agent in agents]
        read = ToolCall("read_shared_file", {"uri": ref.uri, "path": "kernel.py"}, "before")
        assert "error" in (await actions.handler(target)(read)).output
        report = await actions.handler(source)(
            ToolCall(
                "submit_report",
                {"message": "发现一种可复用的方法，尚需确认。", "references": [ref.uri]},
                "progress",
            )
        )
        assert not report.stop and c.actor(source).state == AgentState.RUNNING
        advice = ToolCall(
            "advise_researcher",
            {
                "agent_id": target,
                "message": "可参考这个实现，仍需在你的实验中验证。",
                "references": [ref.uri],
            },
            "advice",
        )
        reply = await actions.handler("rsi")(advice)
        assert not reply.stop
        assert (await actions.handler("rsi")(advice)) == reply
        message_id = reply.output["message_id"]
        assert c.state["inboxes"][target].count(message_id) == 1
        assert message_id not in c.state["inboxes"][other]
        assert [c.actor(agent).current_iteration_id for agent in agents] == nodes
        assert all(c.actor(agent).state == AgentState.RUNNING for agent in agents)
        c.receive_during_turn(target, message_id)
        assert message_id in c.state["delivered"]
        restored = GuidedPassKActions(Coordinator(ResearchStore(tmp_path, c.store.run_id)))
        result = await restored.handler(target)(ToolCall(read.name, read.arguments, "after"))
        assert result.output["content"] == "# original experiment\n"
        with pytest.raises(ValueError, match="not part"):
            restored.read_record(other, ref.uri)
        with pytest.raises(ValueError, match="not part"):
            baseline.read_record(target, ref.uri)
        forbidden = await restored.handler(target)(
            ToolCall(
                "checkout_candidate",
                {"candidate_id": candidate.candidate_id},
                "checkout",
            )
        )
        assert "error" in forbidden.output
        assert not (actions.workspace(target) / "kernel.py").exists()

    asyncio.run(scenario())


def test_leader_can_share_an_exact_native_trace_excerpt(tmp_path: Path) -> None:
    async def scenario() -> None:
        c, _, agents = await setup(tmp_path, 2)
        source, target = agents
        actions = GuidedPassKActions(c)
        actor = c.actor(source)
        root = c.store.paths.agent(source) / "sessions" / (actor.session_id or source)
        journal = JournalStateStore(root / "runtime/data/state")
        snapshot = RunSnapshot(
            run_id="native",
            session_id="session",
            profile="default",
            status=RunStatus.QUEUED,
            last_sequence=-1,
        )
        await journal.create_run(snapshot)
        event = RunEvent(
            event_id="e1",
            sequence=0,
            time=datetime.now(UTC),
            run_id="native",
            session_id="session",
            correlation_id="native",
            type=EventType.TOOL_COMPLETED,
            data={"name": "exec_command", "value": "actual diagnostic: correctness failed"},
        )
        await journal.commit_event(
            event,
            snapshot.model_copy(update={"last_sequence": 0}),
            outbox_sinks=(),
        )
        try:
            page = await actions.handler("rsi")(
                ToolCall(
                    "read_researcher_trace",
                    {"agent_id": source, "offset": -1},
                    "trace",
                )
            )
            value = page.output
            assert "actual diagnostic: correctness failed" in value["content"]
            assert value["agent_id"] == source and value["next_offset"] is None
            await actions.handler("rsi")(
                ToolCall(
                    "advise_researcher",
                    {
                        "agent_id": target,
                        "message": "请核对这次诊断的条件。",
                        "references": [value["uri"]],
                    },
                    "share-trace",
                )
            )
            assert actions.read_record(target, value["uri"])["content"] == value["content"]
            assert "read_researcher_trace" not in {t["name"] for t in actions.definitions(target)}
        finally:
            journal.close()

    asyncio.run(scenario())
