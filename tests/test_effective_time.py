"""Budget fairness, outage telemetry and independent research cutoffs."""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from areal_agent_runtime.adapters.codex_cli.runtime import CodexCliExecutionChannel
from test_passk import setup
from test_research_runtime_package import ResponsesModel

from areal_rsi.api.interfaces import ResearchAgentRuntime, ToolCall, ToolResult, TurnResult
from areal_rsi.api.records import AgentState
from areal_rsi.api.research import CoordinationState, TaskBrief
from areal_rsi.infra.coordination.config import ResearchConfig
from areal_rsi.infra.coordination.effective_time import EffectiveTime
from areal_rsi.infra.coordination.engine import ToolSet
from areal_rsi.infra.coordination.evaluation.jobs import ResearchExecution
from areal_rsi.infra.coordination.policies.legacy_passk import PassKEngine
from areal_rsi.infra.execution import ApprovedCommand
from areal_rsi.infra.runtime.research_runtime import ResearchRuntime
from areal_rsi.infra.storage.files import decode, encode, write_json


def test_baseline_union_and_worker_restart_do_not_double_subtract() -> None:
    state = CoordinationState()
    timer = EffectiveTime(state)
    timer.open_worker(0)
    timer.start_agent("a", 0)
    timer.queue("a", "gpu1", 10, 40)
    timer.queue("a", "gpu2", 20, 50)
    timer.availability("a", False, 30)
    timer.availability("a", True, 60)
    timer.close_worker(70)
    # Maintenance gap 70..100 is not worker time.
    timer = EffectiveTime(state)
    timer.open_worker(100)
    timer.data.heartbeat_at = 110
    # Deserialize the historic JSON shape, including arrays and per-agent maps.
    state = decode(CoordinationState, {"effective_time": encode(state.effective_time)})
    timer = EffectiveTime(state)
    timer.open_worker(200)  # Recover a crash at the last durable heartbeat.
    result = timer.budget("a", 220, 100)
    assert result["worker_wall_seconds"] == 100
    assert result["gpu_queue_seconds"] == 40
    assert result["api_wait_seconds"] == 30
    assert result["excluded_union_seconds"] == 50
    assert result["effective_seconds"] == 50


def test_native_retry_signal_excludes_context_errors_and_usage_replay() -> None:
    async def scenario() -> None:
        # Exercise the patched, installed Runtime projection itself.
        channel: Any = object.__new__(CodexCliExecutionChannel)
        channel._api_available = None
        channel._state = SimpleNamespace(token_total={"totalTokens": 10})
        events = []

        async def emit(kind: Any, data: Any) -> None:
            events.append(data)

        channel._emit = emit
        await channel._project("error", {"message": "context window exceeded"})
        assert not events
        await channel._project("error", {"message": "429", "willRetry": True})
        await channel._project("error", {"message": "503", "willRetry": True})
        assert events == [{"vendor": "codex-cli", "apiAvailable": False}]
        await channel._project("item/reasoning/textDelta", {"delta": "progress"})
        await channel._project("item/agentMessage/delta", {"delta": "progress"})
        assert events[-1]["apiAvailable"] is True and len(events) == 2

    asyncio.run(scenario())


def test_each_researcher_stops_at_own_budget_excluding_pauses_and_leader_waits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Runtime:
        def for_interaction(self) -> Runtime:
            return self

        def configuration(self) -> dict[str, Any]:
            return {}

        def has_session(self, session: Path) -> bool:
            return False

        async def turn(self, *args: Any, **kwargs: Any) -> TurnResult:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    async def scenario() -> None:
        c, actions, agents = await setup(tmp_path, 2)
        brief = c.store.brief()
        brief.limits.max_wall_seconds = None
        brief.limits.max_effective_seconds = 60
        # setup's brief reference is authoritative; write through that path.
        write_json(c.store.root / "briefs" / "initial.json", brief)
        clock = [100.0]
        monkeypatch.setattr(
            "areal_rsi.infra.coordination.coordinator.now",
            lambda: datetime.fromtimestamp(clock[0], UTC).isoformat(),
        )
        engine = PassKEngine(
            c,
            cast(ResearchAgentRuntime, Runtime()),
            lambda a: ToolSet([], actions.handler(a)),
            clock=lambda: clock[0],
        )
        try:
            await engine.tick()
            await asyncio.sleep(0.01)
            assert engine.effective is not None
            timer = engine.effective
            clock[0] = 105
            c.pause()
            assert timer.budget(agents[0], 205, 60)["effective_seconds"] == 5
            clock[0] = 205
            c.resume()
            timer.queue(agents[1], "queued", 210, 240)
            clock[0] = 261
            await engine.tick()
            assert c.actor(agents[0]).state == AgentState.STOPPED
            assert c.actor(agents[1]).state != AgentState.STOPPED
            assert c.state.status == "running" and c.actor("rsi").state != AgentState.STOPPED
            leader_budget = actions.state("rsi")["budget"]
            own_budget = actions.state(agents[1])["budget"]
            assert "elapsed_seconds" not in leader_budget and "elapsed_seconds" not in own_budget
            assert "remaining_wall_seconds" not in own_budget
            assert own_budget["effective_time"]["remaining_effective_seconds"] == 29
            assert "researchers" not in own_budget
            assert leader_budget["researchers"][agents[1]] == own_budget["effective_time"]
            assert "elapsed_seconds" in c.budget(), "Operator view retains total wall time"
            clock[0] = 291
            await engine.tick()
            assert c.state.status == "stopped"
            assert all(c.actor(a).state == AgentState.STOPPED for a in ["rsi", *agents])
            assert timer.budget(agents[0], 999, 60)["effective_seconds"] == 61
            assert timer.budget(agents[1], 999, 60)["effective_seconds"] == 61
        finally:
            await engine.close()

    asyncio.run(scenario())


def test_real_codex_retry_reports_outage_and_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RSI_API_KEY", "fixture")
    model = ResponsesModel()
    model.fail_requests = 5

    async def scenario() -> None:
        runtime = ResearchRuntime(
            ResearchConfig(
                TaskBrief("test", "test"),
                backend="codex",
                base_url=model.url,
                model="gpt-5.6-luna",
                model_settings_pinned=True,
            )
        )
        events: list[dict[str, Any]] = []

        async def emit(event: dict[str, Any]) -> None:
            events.append(event)

        async def handle(call: Any) -> ToolResult:
            return ToolResult({})

        try:
            result = await asyncio.wait_for(
                runtime.turn(
                    tmp_path / "session",
                    system_prompt="Complete without tools",
                    user_message="Say Done",
                    message_id="retry",
                    tools=[],
                    handler=handle,
                    workspace=tmp_path / "workspace",
                    event_sink=emit,
                ),
                60,
            )
            assert result.text == "Done"
            availability = [e for e in events if e["type"] == "api_availability"]
            assert [e["available"] for e in availability] == [False, True]
            assert availability[1]["timestamp"] > availability[0]["timestamp"]
        finally:
            await runtime.close()

    try:
        asyncio.run(scenario())
    finally:
        model.close()


def test_failed_local_job_closes_stale_remote_queue(tmp_path: Path) -> None:
    async def scenario() -> None:
        c, actions, agents = await setup(tmp_path, 1)
        brief = c.store.brief()
        brief.limits.max_effective_seconds = 60
        write_json(c.store.root / "briefs/initial.json", brief)
        timer = EffectiveTime(c.state)
        timer.open_worker(time.time() - 20)
        timer.start_agent(agents[0], time.time() - 20)
        command = ApprovedCommand(
            "test",
            (
                sys.executable,
                "-c",
                "import json,sys,time; from pathlib import Path; "
                "Path(sys.argv[1], 'queue.json').write_text(json.dumps("
                "dict(created_at=time.time()-5, started_at=None, finished_at=None))); "
                "sys.exit(1)",
                "{output}",
            ),
            ("output",),
            queue_timing_file="queue.json",
        )
        execution = ResearchExecution(actions, commands={command.name: command})
        try:
            c.begin_turn(agents[0])
            result = await actions.handler(agents[0])(
                ToolCall(
                    "run_job",
                    {"command_name": "test", "parameters_json": "{}", "wait": True},
                    "job",
                )
            )
            record = execution.manager.get(result.output["job_id"])
            assert record.state == "failed" and record.finished_at is not None
            await execution.poll()
            spans = timer.data.agents[agents[0]].queue_spans
            assert spans["remote:" + record.job_id][1] == record.finished_at
            before = timer.budget(agents[0], record.finished_at, 60)
            after = timer.budget(agents[0], record.finished_at + 30, 60)
            assert after["effective_seconds"] - before["effective_seconds"] == pytest.approx(30)
        finally:
            execution.close(cancel=True)

    asyncio.run(scenario())
