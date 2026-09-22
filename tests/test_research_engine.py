"""Scheduler contracts at the runtime boundary; native execution has integration tests."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from runtime_fakes import CallbackRuntime

from areal_rsi.api.interfaces import AgentRuntimeError, ToolCall, ToolResult, TurnResult
from areal_rsi.api.records import AgentState
from areal_rsi.api.research import ResearchLimits, ResearchReport, TaskBrief
from areal_rsi.infra.coordination.coordinator import Coordinator
from areal_rsi.infra.coordination.engine import ResearchEngine, ToolSet
from areal_rsi.infra.coordination.tools.actions import definition

Json = dict[str, Any]


def coordinator(root: Path, limits: ResearchLimits | None = None) -> Coordinator:
    c = Coordinator.create(
        root, TaskBrief("Research", "Inspect contracts", limits or ResearchLimits())
    )
    c.start()
    return c


def tools(c: Coordinator, agent: str) -> ToolSet:
    async def handle(call: ToolCall) -> ToolResult:
        if call.name == "report":
            return ToolResult(c.report(agent, key=call.call_id, fields={"message": "Checked"}),
                              stop=True)
        c.wait(agent, "Waiting for relevant work")
        return ToolResult("waiting", stop=True)

    return ToolSet([definition("report", "Report"), definition("wait", "Wait")], handle)


def result(session: Path, delivery: Json, text: str = "", reason: str = "waiting") -> TurnResult:
    return TurnResult(session.name, delivery["message_id"], text, reason, {})


async def finish(session: Path, delivery: Json, tool: str = "wait") -> TurnResult:
    await delivery["handler"](ToolCall(tool, {}, delivery["message_id"]))
    return result(session, delivery)


def waiting_researcher(c: Coordinator, key: str) -> str:
    agent = c.spawn(key=key, direction=key, instruction="Check " + key)["agent_id"]
    c.begin_turn(agent)
    c.end_turn(agent)
    return agent


@pytest.mark.parametrize("recipient_role", ["rsi", "research"])
@pytest.mark.parametrize("update_sender", ["peer", "system"])
def test_delivery_origin_and_updates_do_not_reassign_active_work(
    tmp_path: Path, recipient_role: str, update_sender: str
) -> None:
    async def scenario() -> None:
        c = coordinator(tmp_path, ResearchLimits(max_agents=2))
        c.begin_turn("rsi")
        source = waiting_researcher(c, "component")
        recipient = "rsi" if recipient_role == "rsi" else waiting_researcher(c, "combine")
        c.end_turn("rsi")
        message = c.enqueue(source, recipient, "Initial evidence", key="first")
        node = c.actor(recipient).current_iteration_id
        update: str | None = None

        async def run(session: Path, delivery: Json) -> TurnResult:
            nonlocal update
            agent = delivery["recovery_state"]["agent_id"]
            if agent != recipient:
                return await finish(session, delivery)
            envelope = json.loads(delivery["user_message"])
            assert envelope["sender"] == source
            assert envelope["sender_direction"] == "component"
            assert envelope["recipient"] == recipient and envelope["message_id"] == message
            sender = source if update_sender == "peer" else "system"
            update = c.enqueue(sender, recipient, "New evidence", key="update")
            pending = delivery["incoming_messages"]()
            assert [item["message_id"] for item in pending] == [update]
            assert pending[0]["sender"] == sender
            if sender == source:
                assert pending[0]["sender_direction"] == "component"
            await delivery["event_sink"]({"type": "message_injected", "message_id": update})
            assert not delivery["incoming_messages"]()
            assert c.actor(recipient).current_iteration_id == node
            return await finish(session, delivery, "report" if recipient != "rsi" else "wait")

        runtime = CallbackRuntime(run)
        engine = ResearchEngine(c, runtime, lambda agent: tools(c, agent))
        await engine.run_until_idle(timeout=3)
        assert update is not None
        assert c.state.delivered.count(message) == c.state.delivered.count(update) == 1
        assert not c.state.inboxes[recipient]
        count = len(runtime.calls)
        await engine.run_until_idle(timeout=3)
        assert len(runtime.calls) == count
        if recipient != "rsi":
            assert c.actor(recipient).wait_reason == "rsi_reply"
            assert len(c.actor(recipient).iterations) == 1

    asyncio.run(scenario())


def test_literal_user_message_is_not_wrapped(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = coordinator(tmp_path)
        c.begin_turn("rsi")
        c.end_turn("rsi")
        c.enqueue("user", "rsi", "Continue these experiments", key="user")

        async def run(session: Path, delivery: Json) -> TurnResult:
            assert delivery["user_message"] == "Continue these experiments"
            return await finish(session, delivery)

        await ResearchEngine(c, CallbackRuntime(run), lambda agent: tools(c, agent)).run_until_idle(
            timeout=3
        )

    asyncio.run(scenario())


def test_queued_updates_wait_for_next_assignment_across_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = coordinator(tmp_path, ResearchLimits(max_agents=2))
        c.begin_turn("rsi")
        source = waiting_researcher(c, "source")
        recipient = c.spawn(key="recipient", direction="combine", instruction="First phase")[
            "agent_id"
        ]
        c.end_turn("rsi")
        updates: list[str] = []
        researcher_calls = 0

        async def run(session: Path, delivery: Json) -> TurnResult:
            nonlocal researcher_calls
            if delivery["recovery_state"]["agent_id"] == "rsi":
                return await finish(session, delivery)
            researcher_calls += 1
            if researcher_calls == 1:
                updates.extend([
                    c.enqueue(source, recipient, "Late peer evidence", key="late-peer"),
                    c.enqueue("system", recipient, "Late job completion", key="late-job"),
                ])
            else:
                assert researcher_calls == 2
                assert "Next phase" in delivery["user_message"]
                assert len(c.actor(recipient).iterations) == 2
                pending = delivery["incoming_messages"]()
                assert [item["message_id"] for item in pending] == updates
                for item in pending:
                    await delivery["event_sink"](
                        {"type": "message_injected", "message_id": item["message_id"]}
                    )
            # A terminal report must leave late updates queued for the next assignment.
            return await finish(session, delivery, "report")

        runtime = CallbackRuntime(run)
        await ResearchEngine(c, runtime, lambda agent: tools(c, agent)).run_until_idle(timeout=3)
        assert c.actor(recipient).wait_reason == "rsi_reply"
        assert c.state.inboxes[recipient] == updates
        c = Coordinator(c.store)
        assert recipient not in c.pending()
        c.assign(recipient, key="next", instruction="Next phase")
        await ResearchEngine(c, runtime, lambda agent: tools(c, agent)).run_until_idle(timeout=3)
        assert researcher_calls == 2
        assert all(c.state.delivered.count(identifier) == 1 for identifier in updates)
        assert not c.state.inboxes[recipient]

    asyncio.run(scenario())


def test_report_publication_survives_crash_before_delivery_ack(tmp_path: Path) -> None:
    c = coordinator(tmp_path)
    agent = c.spawn(key="one", direction="inspect", instruction="Inspect")["agent_id"]
    message, _ = c.begin_turn(agent)
    c.enqueue("system", agent, "Late result", key="late")
    c.report(agent, key="report", fields={"message": "Checked"})
    c = Coordinator(c.store)
    assert agent in c.pending()
    resumed, interrupted = c.begin_turn(agent)
    assert interrupted and resumed.message_id == message.message_id
    c.report(agent, key="report", fields={"message": "Checked"})
    c.end_turn(agent)
    assert agent not in c.pending()
    assert c.actor(agent).wait_reason == "rsi_reply"
    assert len(c.store.records("reports", ResearchReport)) == 1


def test_default_budget_does_not_stop_after_four_hours(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = coordinator(tmp_path)
        assert c.state.started_at is not None
        observed = datetime.fromisoformat(c.state.started_at).timestamp() + 86400
        runtime = CallbackRuntime(finish)
        engine = ResearchEngine(c, runtime, lambda agent: tools(c, agent), clock=lambda: observed)
        try:
            await engine.run_until_idle(timeout=3)
            assert runtime.calls
            assert c.state.status == "running"
            assert not c.state.budget_stop_reason
            budget = c.budget(observed_at=observed)
            assert budget["remaining_wall_seconds"] is None
            assert budget["deadline_at"] is None
        finally:
            await engine.close()

    asyncio.run(scenario())


def test_wall_budget_keeps_deadline_across_wait_pause_and_resume(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = coordinator(tmp_path, ResearchLimits(max_wall_seconds=1800))
        assert c.state.started_at is not None
        started = datetime.fromisoformat(c.state.started_at).timestamp()
        clock = [started + 1200]
        snapshots: list[Json] = []

        async def run(session: Path, delivery: Json) -> TurnResult:
            snapshots.append(dict(delivery["recovery_state"]["budget"]))
            return await finish(session, delivery)

        runtime = CallbackRuntime(run)
        engine = ResearchEngine(c, runtime, lambda agent: tools(c, agent), clock=lambda: clock[0])
        await engine.run_until_idle(timeout=3)
        assert snapshots[-1]["elapsed_seconds"] == 1200
        assert snapshots[-1]["remaining_wall_seconds"] == 600
        c.pause()
        clock[0] += 300
        c.resume()
        c.enqueue("user", "rsi", "Check remaining work", key="next")
        await engine.run_until_idle(timeout=3)
        assert snapshots[-1]["remaining_wall_seconds"] == 300
        assert snapshots[-1]["deadline_at"] == snapshots[0]["deadline_at"]
        clock[0] = started + 1801
        await engine.tick()
        assert c.state.status == "paused"
        assert c.state.budget_stop_reason == "Run wall-time budget exhausted"
        assert len(runtime.calls) == 2
        await engine.close()

    asyncio.run(scenario())


def test_pause_cancels_turn_and_resume_keeps_delivery_and_session(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = coordinator(tmp_path)
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def run(session: Path, delivery: Json) -> TurnResult:
            if len(runtime.calls) == 1:
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            return await finish(session, delivery)

        runtime = CallbackRuntime(run)
        engine = ResearchEngine(c, runtime, lambda agent: tools(c, agent))
        try:
            await engine.tick()
            await asyncio.wait_for(started.wait(), 2)
            original = c.state.active["rsi"]
            c.pause()
            await engine.tick()
            assert cancelled.is_set()
            assert c.state.active["rsi"] == original
            assert c.actor("rsi").state == AgentState.PAUSED
            c.resume()
            await engine.run_until_idle(timeout=3)
            assert len(runtime.calls) == 2
            first, second = runtime.calls
            assert first[0] == second[0]
            assert first[1]["message_id"] == second[1]["message_id"] == original
            assert first[1]["user_message"] and second[1]["user_message"] is None
            assert c.state.delivered.count(original) == 1
        finally:
            await engine.close()

    asyncio.run(scenario())


def test_final_reply_archives_report_and_followup_reuses_session(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = coordinator(tmp_path)
        agent = c.spawn(key="one", direction="inspect", instruction="Check inputs")["agent_id"]
        calls: list[tuple[Path, Json]] = []

        async def run(session: Path, delivery: Json) -> TurnResult:
            if delivery["recovery_state"]["agent_id"] == "rsi":
                return await finish(session, delivery)
            calls.append((session, delivery))
            if len(calls) == 2:
                assert "Check outputs" in delivery["user_message"]
                assignment = delivery["recovery_state"]["current_assignment"]
                assert assignment["instruction"] == "Check outputs"
            return result(session, delivery, "The contract matches", "completed")

        runtime = CallbackRuntime(run)
        engine = ResearchEngine(c, runtime, lambda actor: tools(c, actor))
        await engine.run_until_idle(timeout=3)
        assert c.actor(agent).wait_reason == "rsi_reply"
        reports = c.store.records("reports", ResearchReport)
        assert len(reports) == 1 and reports[0].message == "The contract matches"
        await engine.run_until_idle(timeout=3)
        assert len(calls) == 1
        c.assign(agent, key="next", instruction="Check outputs")
        await engine.run_until_idle(timeout=3)
        assert len(calls) == 2 and calls[0][0] == calls[1][0]
        assert calls[0][1]["message_id"] != calls[1][1]["message_id"]
        assert len(c.store.records("reports", ResearchReport)) == 2
        assert len(c.actor(agent).iterations) == 2
        assert agent not in c.state.turn_failures

    asyncio.run(scenario())


def test_native_usage_is_deduplicated_across_restart_and_pauses_at_limit(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = coordinator(tmp_path, ResearchLimits(max_total_tokens=20))

        async def run(session: Path, delivery: Json) -> TurnResult:
            return await finish(session, delivery)

        runtime = CallbackRuntime(run)
        engine = ResearchEngine(c, runtime, lambda actor: tools(c, actor))
        event = {"type": "runtime_usage", "event_id": "native-1", "usage": {"total_tokens": 12}}
        await engine._event("rsi", event)
        assert c.state.total_tokens == 12
        c = Coordinator(c.store)
        engine = ResearchEngine(c, runtime, lambda actor: tools(c, actor))
        await engine._event("rsi", event)
        assert c.state.total_tokens == 12
        await engine._event("rsi", {**event, "event_id": "native-2"})
        assert c.state.total_tokens == 24 and c.state.status == "paused"
        assert c.state.budget_stop_reason == "Run token budget exhausted"
        assert await engine.tick() == 0
        assert not runtime.calls and not c.state.model_requests
        await engine.close()

    asyncio.run(scenario())


def test_execution_lock_excludes_another_scheduler(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = coordinator(tmp_path)

        async def run(session: Path, delivery: Json) -> TurnResult:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        runtime = CallbackRuntime(run)
        first = ResearchEngine(c, runtime, lambda actor: tools(c, actor))
        second = ResearchEngine(Coordinator(c.store), runtime, lambda actor: tools(c, actor))
        try:
            await first.tick()
            with pytest.raises(AgentRuntimeError, match="Another live worker"):
                await second.tick()
        finally:
            await first.close()
            await second.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("save_fails", [False, True])
def test_close_releases_ownership_and_cannot_overwrite_successor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, save_fails: bool
) -> None:
    async def scenario() -> None:
        c = coordinator(tmp_path, ResearchLimits(max_effective_seconds=60))

        async def run(session: Path, delivery: Json) -> TurnResult:
            return await finish(session, delivery)

        runtime = CallbackRuntime(run)
        first = ResearchEngine(c, runtime, lambda actor: tools(c, actor))
        first._acquire()
        with monkeypatch.context() as patch:
            if save_fails:
                def fail_save() -> None:
                    raise OSError("disk full")

                patch.setattr(c, "save", fail_save)
                with pytest.raises(OSError, match="disk full"):
                    await first.close()
            else:
                await first.close()
        successor = Coordinator(c.store)
        second = ResearchEngine(successor, runtime, lambda actor: tools(successor, actor))
        try:
            second._acquire()
            successor.state.total_tokens = 17
            successor.save()
            await first.close()
            assert json.loads(c.path.read_text())["total_tokens"] == 17
        finally:
            await second.close()
            await first.close()

    asyncio.run(scenario())


def test_failed_worker_initialization_releases_execution_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        c = coordinator(tmp_path, ResearchLimits(max_effective_seconds=60))

        async def run(session: Path, delivery: Json) -> TurnResult:
            return await finish(session, delivery)

        runtime = CallbackRuntime(run)
        first = ResearchEngine(c, runtime, lambda actor: tools(c, actor))
        second = ResearchEngine(Coordinator(c.store), runtime, lambda actor: tools(c, actor))
        try:
            with monkeypatch.context() as patch:
                def fail_save() -> None:
                    raise OSError("disk full")

                patch.setattr(c, "save", fail_save)
                with pytest.raises(OSError, match="disk full"):
                    first._acquire()
            second._acquire()
        finally:
            await second.close()
            await first.close()

    asyncio.run(scenario())


def test_usage_replay_does_not_rebuild_full_recovery_state(tmp_path: Path) -> None:
    """Native cancellation replays settled usage; accounting must stay cheap and exact."""
    async def scenario() -> None:
        c = coordinator(tmp_path)
        refreshes = 0
        observations: dict[str, int] = {}

        class CountingEngine(ResearchEngine):
            def _recovery_state(self, agent_id: str) -> Json:
                nonlocal refreshes
                refreshes += 1
                return super()._recovery_state(agent_id)

        async def run(session: Path, delivery: Json) -> TurnResult:
            initial = refreshes
            event = {
                "type": "runtime_usage", "event_id": "settled-native-event",
                "usage": {"total_tokens": 12},
            }
            for _ in range(20):
                await delivery["event_sink"](event)
            observations["tokens"] = c.state.total_tokens
            observations["usage_refreshes"] = refreshes - initial
            # Meaningful tool boundaries still refresh the context, including current usage.
            await delivery["event_sink"]({"type": "tool_completed", "name": "probe"})
            observations["tool_refreshes"] = refreshes - initial
            observations["context_tokens"] = delivery["recovery_state"]["budget"]["total_tokens"]
            return await finish(session, delivery)

        engine = CountingEngine(c, CallbackRuntime(run), lambda actor: tools(c, actor))
        await engine.run_until_idle(timeout=3)
        await engine.close()
        assert observations == {
            "tokens": 12, "usage_refreshes": 0, "tool_refreshes": 1, "context_tokens": 12,
        }

    asyncio.run(scenario())
