"""Run-local collaboration preserves independent writes and cross-run isolation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from areal_agent_runtime.contracts.events import EventType, RunEvent
from areal_agent_runtime.contracts.product import RunSnapshot, RunStatus
from test_passk import ScriptedRuntime, setup

from areal_rsi.api.interfaces import ToolCall
from areal_rsi.api.records import AgentState, RecordRef
from areal_rsi.infra.coordination.coordinator import Coordinator
from areal_rsi.infra.coordination.engine import ToolSet
from areal_rsi.infra.coordination.policies.collaboration import build_plan
from areal_rsi.infra.coordination.policies.leader_guidance import GuidedPassKActions
from areal_rsi.infra.coordination.policies.passk import PassKActions, PassKEngine
from areal_rsi.infra.coordination.store import ResearchStore
from areal_rsi.infra.runtime.runtime_storage import JournalStateStore


def test_team_reads_and_targeted_advice_preserve_independent_attempts(tmp_path: Path) -> None:
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
        read = ToolCall(
            "read_candidate_file",
            {"candidate_id": candidate.candidate_id, "path": "kernel.py"},
            "before",
        )
        assert (await actions.handler(target)(read)).output["content"] == "# original experiment\n"
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
        assert restored.read_record(other, ref.uri)["candidate_id"] == candidate.candidate_id
        last = restored.state("rsi")["researchers"][1]["latest_advice"]
        assert last["message_id"] == message_id and last["references"] == [ref.uri]
        assert {a["agent_id"] for a in restored.state(target)["researchers"]} == set(agents)
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
        imported = await restored.handler(target)(
            ToolCall(
                "import_candidate",
                {"candidate_id": candidate.candidate_id, "destination": "peer-copy"},
                "import",
            )
        )
        assert "error" not in imported.output
        assert (actions.workspace(target) / "peer-copy/kernel.py").read_text() == (
            "# original experiment\n"
        )
        assert not (actions.workspace(target) / "kernel.py").exists()
        blocked = await baseline.handler(other)(
            ToolCall("import_candidate", read.arguments | {"destination": "peer"}, "import")
        )
        assert "error" in blocked.output
        updated = await actions.handler("rsi")(
            ToolCall(
                "update_plan",
                {"plan": "保留布局探索，先验证尾部索引。", "reason": "已有实验尚未验证这点。"},
                "plan",
            )
        )
        assert "error" not in updated.output
        restored = GuidedPassKActions(Coordinator(ResearchStore(tmp_path, c.store.run_id)))
        engine = PassKEngine(
            restored.c, ScriptedRuntime(), lambda a: ToolSet([], restored.handler(a))
        )
        assert (
            restored.state("rsi")["plan"]
            == engine._recovery_state("rsi")["plan"]
            == ("保留布局探索，先验证尾部索引。")
        )

    asyncio.run(scenario())


def test_researchers_can_inspect_same_run_traces_without_leader_permission(tmp_path: Path) -> None:
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
        await journal.commit_event(
            event.model_copy(
                update={
                    "event_id": "e2",
                    "sequence": 1,
                    "type": EventType.TOOL_FAILED,
                    "data": {"name": "exec_command", "error": "Compiler rejected the experiment"},
                }
            ),
            snapshot.model_copy(update={"last_sequence": 1}),
            outbox_sinks=(),
        )
        try:
            page = await actions.handler(target)(
                ToolCall(
                    "read_researcher_trace",
                    {"agent_id": source, "offset": -1},
                    "trace",
                )
            )
            value = page.output
            assert "actual diagnostic: correctness failed" in value["content"]
            assert "Compiler rejected the experiment" in value["content"]
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
            assert "read_researcher_trace" in {t["name"] for t in actions.definitions(target)}
        finally:
            journal.close()

    asyncio.run(scenario())


def test_leader_and_researcher_cannot_read_or_share_other_runs(tmp_path: Path) -> None:
    async def scenario() -> None:
        c, _, agents = await setup(tmp_path, 1)
        foreign, _, _ = await setup(tmp_path, 1)
        actions = GuidedPassKActions(c)
        ref = foreign.store.put("materials", "private", {"content": "other run secret"})
        for actor in ["rsi", *agents]:
            for uri in [
                ref.uri,
                ref.uri.replace("/materials/", "/materials/../materials/"),
                "run://"
                + c.store.run_id
                + "/materials/../../"
                + foreign.store.run_id
                + "/materials/private.json",
                "run://" + c.store.run_id + "/materials/nested/private.json",
            ]:
                reply = await actions.handler(actor)(
                    ToolCall("read_record", {"uri": uri}, "read-" + uri)
                )
                assert "error" in reply.output and "other run secret" not in str(reply.output)
        reply = await actions.handler("rsi")(
            ToolCall(
                "advise_researcher",
                {"agent_id": agents[0], "message": "see evidence", "references": [ref.uri]},
                "bad-share",
            )
        )
        assert "error" in reply.output
        reply = await actions.handler(agents[0])(
            ToolCall(
                "read_researcher_trace",
                {"agent_id": "../" + foreign.store.run_id, "offset": 0},
                "bad-trace",
            )
        )
        assert "error" in reply.output
        # A filesystem alias must not bypass the run:// boundary either.
        alias = c.store.root / "materials/alias.json"
        alias.parent.mkdir(exist_ok=True)
        alias.symlink_to(foreign.store.root / "materials/private.json")
        with pytest.raises(ValueError, match="escapes"):
            actions.read_record("rsi", "materials/alias.json")
        researcher = c.actor(agents[0])
        root = (
            c.store.paths.agent(researcher.agent_id)
            / "sessions"
            / (researcher.session_id or researcher.agent_id)
            / "runtime/data/state"
        )
        root.mkdir(parents=True)
        (root / "journal.jsonl").symlink_to(foreign.store.root / "materials/private.json")
        with pytest.raises(ValueError, match="escapes"):
            actions.trace(researcher.agent_id, "aliased-trace", 0)

    asyncio.run(scenario())
