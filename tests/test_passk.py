"""Pass@k independence, sustained attempts, trusted stopping and recovery."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from areal_rsi.api.base import AgentState
from areal_rsi.api.research import (
    CandidateSnapshot,
    ResearchLimits,
    TaskBrief,
    VerificationContract,
    VerifiedResult,
)
from areal_rsi.base.storage import FileContentStore, encode, now
from areal_rsi.coordination.config import ResearchConfig
from areal_rsi.coordination.coordinator import Coordinator, effect_id
from areal_rsi.coordination.engine import ToolSet
from areal_rsi.coordination.jobs import ResearchExecution
from areal_rsi.coordination.passk import PassKActions, PassKEngine, launch, stop_on_target
from areal_rsi.execution import ApprovedCommand, JobRecord, JobSpec
from areal_rsi.modules.agent_runtime import ToolCall, TurnResult
from areal_rsi.research_app import ResearchApp


def configuration(k: int = 3, seconds: float = 60) -> ResearchConfig:
    return ResearchConfig(
        TaskBrief(
            "独立完成整题",
            "自行探索，不限提交次数",
            ResearchLimits(
                max_agents=k,
                max_nodes=None,
                max_total_tokens=None,
                max_verify_calls=None,
                max_wall_seconds=seconds,
                max_jobs=k,
            ),
            VerificationContract("speedup", target=1.0),
        ),
        mode="passk",
        model_settings_pinned=True,
    )


def test_portfolio_search_waits_for_own_processed_diagnostic() -> None:
    execution = object.__new__(ResearchExecution)
    execution.require_diagnostic_before_search = True
    execution.jobs = {
        "job-search": {
            "agent_id": "research-a",
            "verification": True,
            "phase": "search",
        },
        "job-other-diagnostic": {
            "agent_id": "research-b",
            "verification": True,
            "phase": "diagnostic",
            "processed": True,
            "result": {"status": "succeeded"},
        },
        "job-own-diagnostic": {
            "agent_id": "research-a",
            "verification": True,
            "phase": "diagnostic",
            "processed": False,
        },
    }
    record = JobRecord("job-search", JobSpec(backend="mock"), "queued", 0, 0)
    assert not execution._job_schedulable(record)
    execution.jobs["job-own-diagnostic"].update(
        processed=True, result={"status": "succeeded"}
    )
    assert execution._job_schedulable(record)


def test_portfolio_scheduler_stops_allocating_after_strict_agent_target() -> None:
    execution = object.__new__(ResearchExecution)
    execution.require_diagnostic_before_search = True
    execution.minimum_search_verifications_per_agent = 2
    execution.jobs = {
        "job-diagnostic": {
            "agent_id": "research-a",
            "verification": True,
            "phase": "diagnostic",
            "processed": True,
            "result": {"status": "succeeded"},
        },
        "job-success": {
            "agent_id": "research-a",
            "verification": True,
            "phase": "search",
            "protocol_eligible": True,
            "processed": True,
            "result": {
                "status": "succeeded",
                "simulated": False,
                "metrics": {"task_count": 20, "failed_count": 0},
            },
        },
        "job-infra-failure": {
            "agent_id": "research-a",
            "verification": True,
            "phase": "search",
            "protocol_eligible": True,
            "processed": True,
            "result": {
                "status": "succeeded",
                "simulated": False,
                "metrics": {"task_count": 20, "failed_count": 1},
            },
        },
        "job-active": {
            "agent_id": "research-a",
            "verification": True,
            "phase": "search",
            "protocol_eligible": True,
            "processed": False,
        },
        "job-queued": {
            "agent_id": "research-a",
            "verification": True,
            "phase": "search",
            "protocol_eligible": True,
            "processed": False,
        },
    }
    records = {
        "job-active": JobRecord("job-active", JobSpec(backend="mock"), "running", 0, 0),
        "job-queued": JobRecord("job-queued", JobSpec(backend="mock"), "queued", 0, 0),
    }
    execution.find_job = records.get  # type: ignore[method-assign]

    assert not execution._job_schedulable(records["job-queued"])
    records["job-active"] = JobRecord(
        "job-active", JobSpec(backend="mock"), "failed", 0, 0
    )
    assert execution._job_schedulable(records["job-queued"])


def test_trusted_search_success_rejects_harness_failures_and_simulation() -> None:
    info: dict[str, Any] = {
        "verification": True,
        "phase": "search",
        "protocol_eligible": True,
        "result": {
            "status": "succeeded",
            "simulated": False,
            "metrics": {"task_count": 20, "failed_count": 0},
        },
    }
    assert ResearchExecution._trusted_search_success(info)
    info["result"]["metrics"]["failed_count"] = 1
    assert not ResearchExecution._trusted_search_success(info)
    info["result"]["metrics"]["failed_count"] = 0
    info["result"]["simulated"] = True
    assert not ResearchExecution._trusted_search_success(info)


def test_prediagnostic_search_is_not_protocol_eligible(tmp_path: Path) -> None:
    execution = object.__new__(ResearchExecution)
    execution.require_diagnostic_before_search = True
    execution.jobs = {
        "job-diagnostic": {
            "agent_id": "research-a",
            "verification": True,
            "phase": "diagnostic",
            "processed": False,
        }
    }
    assert not execution._diagnostic_ready("research-a")
    execution.jobs["job-diagnostic"].update(
        processed=True, result={"status": "succeeded"}
    )
    assert execution._diagnostic_ready("research-a")


def test_search_protocol_requires_distinct_candidate_content() -> None:
    execution = object.__new__(ResearchExecution)
    execution.require_diagnostic_before_search = True
    digests = {
        "candidate-baseline": "sha256:baseline",
        "candidate-first": "sha256:first",
        "candidate-first-copy": "sha256:first",
        "candidate-second": "sha256:second",
    }
    execution._candidate_semantic_digest = lambda candidate_id: digests.get(candidate_id)  # type: ignore[method-assign]
    execution.jobs = {
        "job-diagnostic": {
            "agent_id": "research-a",
            "candidate_id": "candidate-baseline",
            "verification": True,
            "phase": "diagnostic",
            "processed": True,
            "result": {"status": "succeeded"},
        },
        "job-first": {
            "agent_id": "research-a",
            "candidate_id": "candidate-first",
            "verification": True,
            "phase": "search",
            "protocol_eligible": True,
        },
    }

    assert not execution._search_protocol_eligible("research-a", "candidate-baseline")
    assert not execution._search_protocol_eligible("research-a", "candidate-first-copy")
    assert execution._search_protocol_eligible("research-a", "candidate-second")
    assert execution._search_protocol_eligible(
        "research-a", "candidate-first", before_job_id="job-first"
    )


def test_search_protocol_deduplicates_across_agents_but_allows_infra_retry() -> None:
    execution = object.__new__(ResearchExecution)
    execution.require_diagnostic_before_search = True
    digests = {
        "baseline-a": "sha256:baseline-a",
        "candidate-a": "sha256:shared",
        "candidate-b": "sha256:shared",
    }
    execution._candidate_semantic_digest = lambda candidate_id: digests.get(candidate_id)  # type: ignore[method-assign]
    execution.jobs = {
        "job-diagnostic": {
            "agent_id": "research-b",
            "candidate_id": "baseline-a",
            "verification": True,
            "phase": "diagnostic",
            "processed": True,
            "result": {"status": "succeeded"},
        },
        "job-other-agent": {
            "agent_id": "research-a",
            "candidate_id": "candidate-a",
            "verification": True,
            "phase": "search",
            "protocol_eligible": True,
            "processed": True,
            "result": {
                "status": "infra_error",
                "simulated": False,
                "metrics": {},
            },
        },
    }

    assert execution._search_protocol_eligible("research-b", "candidate-b")
    execution.jobs["job-other-agent"]["result"] = {
        "status": "succeeded",
        "simulated": False,
        "metrics": {"task_count": 20, "failed_count": 0},
    }
    assert not execution._search_protocol_eligible("research-b", "candidate-b")


def test_candidate_semantic_digest_ignores_json_formatting(tmp_path: Path) -> None:
    content = FileContentStore(tmp_path / "content")
    compact, formatted = tmp_path / "compact", tmp_path / "formatted"
    compact.mkdir()
    formatted.mkdir()
    (compact / "change.json").write_text("{}")
    (formatted / "change.json").write_text("{\n}\n")
    async def snapshots():
        return await asyncio.gather(content.snapshot(compact), content.snapshot(formatted))

    compact_ref, formatted_ref = asyncio.run(snapshots())
    candidates = {
        "compact": CandidateSnapshot("compact", compact_ref, now(), "research-a", "compact"),
        "formatted": CandidateSnapshot(
            "formatted", formatted_ref, now(), "research-a", "formatted"
        ),
    }
    store = SimpleNamespace(content=content, get=lambda _category, key, _kind: candidates[key])
    execution = object.__new__(ResearchExecution)
    execution.c = SimpleNamespace(store=store)

    assert compact_ref.digest != formatted_ref.digest
    assert execution._candidate_semantic_digest("compact") == (
        execution._candidate_semantic_digest("formatted")
    )


@pytest.mark.parametrize("state", ["queued", "running", "succeeded"])
def test_running_or_successful_diagnostic_blocks_resubmission(state: str) -> None:
    execution = object.__new__(ResearchExecution)
    execution.jobs = {
        "job-diagnostic": {
            "agent_id": "research-a",
            "verification": True,
            "phase": "diagnostic",
            "processed": False,
        }
    }
    execution.find_job = lambda _job_id: JobRecord(  # type: ignore[method-assign]
        "job-diagnostic", JobSpec(backend="mock"), state, 0, 0
    )
    assert execution._diagnostic_blocks_resubmission("research-a")


@pytest.mark.parametrize("state", ["cancelled", "failed", "lost"])
def test_failed_diagnostic_allows_replacement(state: str) -> None:
    execution = object.__new__(ResearchExecution)
    execution.jobs = {
        "job-diagnostic": {
            "agent_id": "research-a",
            "verification": True,
            "phase": "diagnostic",
            "processed": True,
            "result": {"status": "infra_error"},
        }
    }
    execution.find_job = lambda _job_id: JobRecord(  # type: ignore[method-assign]
        "job-diagnostic", JobSpec(backend="mock"), state, 0, 0
    )
    assert not execution._diagnostic_blocks_resubmission("research-a")


async def setup(root: Path, k: int = 3) -> tuple[Coordinator, PassKActions, list[str]]:
    c = Coordinator.create(root, configuration(k).brief)
    c.state["mode"] = "passk"
    c.start()
    actions = PassKActions(c)
    await launch(c, actions)
    return c, actions, [actor.agent_id for actor in c.store.run().research_agents]


def test_job_wait_keeps_turn_and_reuses_job_after_recovery(tmp_path: Path) -> None:
    async def scenario() -> None:
        c, actions, agents = await setup(tmp_path, 1)
        agent = agents[0]
        c.begin_turn(agent)
        execution = ResearchExecution(
            actions,
            commands={"test": ApprovedCommand("test", ("echo", "ok"))},
            simulated=True,
            mock_duration=0.2,
        )
        execution.continuous_wait_seconds = 0
        call = ToolCall(
            "run_job",
            {"command_name": "test", "parameters_json": "{}", "wait": True},
            "one-experiment",
        )
        try:
            result = await actions.handler(agent)(call)
            job_id = result.output["job_id"]
            assert not result.stop
            assert "wait_for_job" in result.output["instruction"]
            assert not execution.waiters(execution.jobs[job_id])
            assert c.actor(agent).state == AgentState.RUNNING
            # Closing the coordinator's manager preserves the actual job.
            execution.close()
            execution = ResearchExecution(
                actions,
                commands={"test": ApprovedCommand("test", ("echo", "ok"))},
                simulated=True,
            )
            execution.continuous_wait_seconds = 10
            result = await asyncio.wait_for(actions.handler(agent)(call), 15)
            assert not result.stop
            assert result.output["job_id"] == job_id and len(execution.jobs) == 1
            again = await actions.handler(agent)(
                ToolCall("wait_for_job", {"job_id": job_id}, "wait-again")
            )
            assert not again.stop and again.output["state"] == "succeeded"
            # The tool result is the feedback: no duplicate wakeup delivery.
            assert not execution.waiters(execution.jobs[job_id])
        finally:
            execution.close(cancel=True)

    asyncio.run(scenario())


def test_continuous_wait_is_cancellable(tmp_path: Path) -> None:
    async def scenario() -> None:
        c, actions, agents = await setup(tmp_path, 1)
        agent = agents[0]
        c.begin_turn(agent)
        execution = ResearchExecution(
            actions,
            commands={"test": ApprovedCommand("test", ("echo", "ok"))},
            simulated=True,
            mock_duration=60,
        )
        try:
            submitted = await actions.handler(agent)(
                ToolCall(
                    "run_job",
                    {"command_name": "test", "parameters_json": "{}", "wait": False},
                    "submit",
                )
            )
            wait = asyncio.create_task(execution.wait_for_result(agent, submitted.output["job_id"]))
            await asyncio.sleep(0.1)
            assert not wait.done()
            wait.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(wait, 1)
            assert not execution.waiters(execution.jobs[submitted.output["job_id"]])
        finally:
            execution.close(cancel=True)

    asyncio.run(scenario())


def test_independent_records_jobs_and_identical_snapshots(tmp_path: Path) -> None:
    async def scenario() -> None:
        c, actions, agents = await setup(tmp_path, 2)
        for agent in agents:
            (actions.workspace(agent) / "code.py").write_text("identical code")
            c.begin_turn(agent)
        first, second = [
            await c.store.snapshot(actions.workspace(a), agent_id=a, description=a) for a in agents
        ]
        verification = VerifiedResult(
            "result",
            first.candidate_id,
            c.store.contract_digest(),
            now(),
            "succeeded",
            passed=True,
            score=0.8,
            phase="search",
        )
        ref = c.store.record_verification(verification)
        c.store.consider_best(verification.result_id)
        assert actions.state(agents[0])["best"]["score"] == 0.8
        state = json.dumps(actions.state(agents[1]))
        assert first.candidate_id not in state and verification.result_id not in state
        assert agents[0] not in state and "total_tokens" not in actions.state(agents[1])["budget"]
        with pytest.raises(ValueError, match="not part"):
            actions.read_record(agents[1], ref.uri)
        with pytest.raises(ValueError, match="not part"):
            actions.candidate(agents[1], first.candidate_id)
        for name in (
            "read_candidate_file",
            "import_candidate",
            "checkout_candidate",
            "verify_candidate",
            "withdraw_candidate",
        ):
            result = await actions.handler(agents[1])(
                ToolCall(name, {"candidate_id": first.candidate_id}, name)
            )
            assert "error" in result.output
        for name in (
            "send_update",
            "share_update",
            "spawn_researcher",
            "save_evidence",
        ):
            result = await actions.handler(agents[0])(ToolCall(name, {}, name))
            assert "error" in result.output
        execution = ResearchExecution(
            actions, commands={"test": ApprovedCommand("test", ("echo", "ok"))}, simulated=True
        )
        try:
            result = await actions.handler(agents[0])(
                ToolCall(
                    "run_job",
                    {
                        "command_name": "test",
                        "candidate_id": "",
                        "parameters_json": "{}",
                        "wait": False,
                    },
                    "job",
                )
            )
            job_id = result.output["job_id"]
            for name in (
                "job_status",
                "wait_for_job",
                "rerun_job",
                "read_job_log",
                "collect_job_output",
            ):
                denied = await actions.handler(agents[1])(ToolCall(name, {"job_id": job_id}, name))
                assert "not part" in denied.output["error"]
        finally:
            execution.close(cancel=True)
        result = await actions.handler(agents[0])(
            ToolCall(
                "withdraw_candidate",
                {
                    "candidate_id": first.candidate_id,
                    "reason": "Counterexample",
                    "references": [],
                },
                "withdraw",
            )
        )
        assert result.output["withdrawn"]
        assert c.store.is_withdrawn(first.candidate_id)
        assert not c.store.is_withdrawn(second.candidate_id), "One attempt must not prune another"
        assert len(c.state["inboxes"]["rsi"]) == 1  # Initial launch request only.

    asyncio.run(scenario())


class ScriptedRuntime:
    backend = "standard"

    def __init__(self, leader_busy: bool = False) -> None:
        self.calls: list[str] = []
        self.cancelled: set[str] = set()
        self.leader_busy = leader_busy

    def for_interaction(self) -> ScriptedRuntime:
        return self

    def has_session(self, session_dir: Path) -> bool:
        return False

    def configuration(self) -> dict[str, Any]:
        return {"backend": "standard"}

    async def reconfigure_session(self, session_dir: Path, revision: str) -> None:
        pass

    async def close(self) -> None:
        pass

    async def turn(self, session_dir: Path, **kwargs: Any) -> TurnResult:
        agent = kwargs["recovery_state"]["agent_id"]
        self.calls.append(agent)
        assert agent != "interaction"
        if agent == "rsi":
            if self.leader_busy:
                await asyncio.Event().wait()
            handler = kwargs["handler"]
            state = await handler(ToolCall("get_state", {}, f"state-{len(self.calls)}"))
            if not state.output["started_researchers"]:
                state = await handler(ToolCall("start_researchers", {}, "start-fixed-team"))
            for researcher in state.output["researchers"]:
                if researcher["needs_continue"]:
                    await handler(
                        ToolCall(
                            "continue_researcher",
                            {"agent_id": researcher["agent_id"]},
                            f"continue-{len(self.calls)}-{researcher['agent_id']}",
                        )
                    )
            return TurnResult(session_dir.name, "supervised", "等待进展", "completed", {})
        if self.calls.count(agent) == 1:
            return TurnResult(session_dir.name, "first", "暂时总结", "completed", {})
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.add(agent)
            raise
        raise AssertionError("unreachable")


def test_full_baseline_gate_keeps_passk_leader_idle(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Coordinator.create(tmp_path, configuration(1).brief)
        c.state.update(mode="passk", full_benchmark_evaluation=True)
        c.enqueue("interaction", "rsi", "start", key="start")
        c.start()
        actions = PassKActions(c)
        runtime = ScriptedRuntime()
        engine = PassKEngine(
            c,
            runtime,
            lambda agent_id: ToolSet(actions.definitions(agent_id), actions.handler(agent_id)),
            poll_interval=0.01,
        )
        try:
            await engine.tick()
            assert runtime.calls == []
            assert not engine._actor_allowed("rsi")
            c.state["full_baseline_ready"] = True
            c.save()
            await engine.tick()
            async with asyncio.timeout(1):
                while not runtime.calls:
                    await asyncio.sleep(0.01)
            assert runtime.calls == ["rsi"]
        finally:
            await engine.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("leader_busy", [False, True])
def test_all_attempts_continue_and_cancel_at_original_deadline(
    tmp_path: Path, leader_busy: bool
) -> None:
    async def scenario() -> None:
        c, actions, agents = await setup(tmp_path)
        original = c.state["started_at"]
        await launch(c, actions)
        assert len(c.store.run().research_agents) == 3
        runtime = ScriptedRuntime(leader_busy)
        clock = datetime.fromisoformat(original).timestamp()
        engine = PassKEngine(
            c,
            runtime,
            lambda a: ToolSet(actions.definitions(a), actions.handler(a)),
            clock=lambda: clock,
            poll_interval=0.01,
        )
        try:
            async with asyncio.timeout(3):
                while any(runtime.calls.count(a) < 2 for a in agents):
                    await engine.tick()
                    await asyncio.sleep(0.01)
            assert all(len(c.actor(a).iterations) == 1 for a in agents)
            assert len(list((c.store.root / "reports").glob("*.json"))) == 3
            assert "rsi" in runtime.calls
            clock += 61
            await engine.tick()
            assert set(agents) == runtime.cancelled
            assert c.state["started_at"] == original
            assert c.state["budget_stop_reason"] == "Run wall-time budget exhausted"
        finally:
            await engine.close()

    asyncio.run(scenario())


def test_only_trusted_passing_search_result_stops_group(tmp_path: Path) -> None:
    async def scenario() -> None:
        c, actions, agents = await setup(tmp_path)
        candidate = await c.store.snapshot(
            actions.workspace(agents[0]), agent_id=agents[0], description="test"
        )
        for i, (passed, score, phase, digest) in enumerate(
            [
                (False, 200, "search", c.store.contract_digest()),
                (True, 200, "diagnostic", c.store.contract_digest()),
                (True, 200, "search", "wrong-contract"),
                (True, 0.99, "search", c.store.contract_digest()),
                (True, 1.0, "search", c.store.contract_digest()),
            ]
        ):
            result = VerifiedResult(
                f"result-{i}",
                candidate.candidate_id,
                digest,
                now(),
                "succeeded",
                passed=passed,
                score=score,
                phase=phase,  # type: ignore[arg-type]
            )
            if digest != c.store.contract_digest():
                with pytest.raises(ValueError, match="different comparison contract"):
                    c.store.record_verification(result)
                assert c.state["status"] == "running"
                continue
            c.store.record_verification(result)
            c.store.consider_best(result.result_id)
            stop_on_target(c)
            assert c.state["status"] == ("succeeded" if i == 4 else "running")
        assert all(c.actor(a).state == AgentState.STOPPED for a in agents)
        assert c.state["passk_target"]["result"]["score"] == 1.0

    asyncio.run(scenario())


def test_workbench_mode_stops_and_rejects_intervention(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = ScriptedRuntime()
        app = ResearchApp(
            tmp_path,
            config=configuration(seconds=0.25),
            runtime_factory=lambda _: runtime,
            allow_simulation=True,
        )
        try:
            detail = await app.create({"simulated": True})
            c = app.coordinator(detail["run_id"])
            await app.start(c)
            c = app.coordinator(detail["run_id"])
            with pytest.raises(ValueError, match="intervention"):
                await app.call("chat", {"run_id": c.store.run_id, "text": "change direction"})
            await asyncio.wait_for(app.active[c.store.run_id].task, 3)
            assert c.state["status"] == "stopped"
            assert len(set(runtime.calls)) == 4  # RSI plus the fixed three attempts.
            assert len(c.store.run().research_agents) == 3
            assert (
                json.loads((c.store.root / "coordination/started-config.json").read_text())["mode"]
                == "passk"
            )
        finally:
            await app.close()

    asyncio.run(scenario())


def test_mode_validation_prevents_shared_budget_interference() -> None:
    config = configuration()
    for name in ("max_total_tokens", "max_verify_calls"):
        setattr(config.brief.limits, name, 10)
        with pytest.raises(ValueError, match="unlimited"):
            ResearchConfig(**{**encode(config), "brief": config.brief})
        setattr(config.brief.limits, name, None)


def test_cli_does_not_end_experiment_during_backoff() -> None:
    from areal_rsi.ui.cli import _idle_reason

    assert (
        _idle_reason(
            {
                "mode": "passk",
                "status": "running",
                "worker_alive": True,
                "agents": [{"state": "failed"}],
            }
        )
        is None
    )
    assert (
        _idle_reason({"mode": "passk", "status": "running", "worker_alive": False})
        == "worker_stopped"
    )


def test_supervisor_reports_do_not_block_or_share_research(tmp_path: Path) -> None:
    async def scenario() -> None:
        from areal_rsi.coordination.passk import CONTINUE_MESSAGE, needs_continue

        c, actions, agents = await setup(tmp_path, 2)
        actor_id = agents[0]
        c.begin_turn(actor_id)
        result = await actions.handler(actor_id)(
            ToolCall(
                "submit_report",
                {"message": "我自己的实验发现", "references": []},
                "progress",
            )
        )
        assert not result.stop
        assert c.actor(actor_id).state == AgentState.RUNNING
        assert len(c.actor(actor_id).iterations) == 1
        report = c.state["passk_reports"][actor_id]
        assert report["message"] == "我自己的实验发现"
        events = [event for event in c.store.events() if event.kind == "report_submitted"]
        assert len(events) == 1
        assert events[0].agent_id == actor_id
        assert events[0].data["report_id"] == result.output["report_id"]
        # Replaying the durable operation must not duplicate the timeline event.
        actions.progress(
            actor_id, effect_id("action", actor_id + ":progress"), "我自己的实验发现", []
        )
        assert len([e for e in c.store.events() if e.kind == "report_submitted"]) == 1
        with pytest.raises(ValueError, match="not part"):
            actions.read_record(agents[1], report["uri"])
        assert not any(
            "我自己的实验发现" in json.loads(p.read_text()).get("text", "")
            for p in (c.store.root / "messages").glob("*.json")
            if json.loads(p.read_text()).get("recipient") == agents[1]
        )
        leader = actions.handler("rsi")
        for name in (
            "send_update",
            "message_researcher",
            "stop_researcher",
            "spawn_researcher",
            "run_job",
            "save_evidence",
        ):
            denied = await leader(ToolCall(name, {}, name))
            assert "error" in denied.output
        assert {tool["name"] for tool in actions.definitions("rsi")} == {
            "get_state",
            "start_researchers",
            "continue_researcher",
            "wait",
        }
        running = await leader(ToolCall("continue_researcher", {"agent_id": actor_id}, "running"))
        assert not running.output["continued"]
        c.end_turn(actor_id)
        actor = c.actor(actor_id)
        actor.wait_reason = "passk_supervision"
        c.save_actor(actor)
        assert needs_continue(c, actor_id)
        continued = await leader(
            ToolCall("continue_researcher", {"agent_id": actor_id}, "continue")
        )
        assert continued.output["continued"]
        envelope = json.loads(
            (c.store.root / "messages" / f"{continued.output['message_id']}.json").read_text()
        )
        assert envelope["text"] == CONTINUE_MESSAGE
        assert envelope["references"] == []
        assert len(c.actor(actor_id).iterations) == 1
        assert actor_id in c.pending(), "A progress report must not block the next delivery"

    asyncio.run(scenario())


def test_researcher_at_personal_search_target_sleeps_until_new_information(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        from areal_rsi.coordination.passk import (
            SEARCH_TARGET_WAIT_REASON,
            needs_continue,
            record_search_targets,
        )

        c, actions, agents = await setup(tmp_path, 2)
        agent_id = agents[0]
        c.begin_turn(agent_id)
        record_search_targets(c, {agent_id: 10, agents[1]: 4}, 10)

        engine = PassKEngine(
            c,
            ScriptedRuntime(),
            lambda a: ToolSet(actions.definitions(a), actions.handler(a)),
        )
        engine._complete_research_turn(
            agent_id, c.actor(agent_id).current_iteration_id, "turn", "."
        )

        actor = c.actor(agent_id)
        assert actor.state == AgentState.WAITING
        assert actor.wait_reason == SEARCH_TARGET_WAIT_REASON
        assert not needs_continue(c, agent_id)
        assert c.state["researchers_at_search_target"][agent_id]["count"] == 10
        assert c.state["status"] == "running", "Other researchers must remain active"
        assert not c.state["inboxes"].get(agent_id)

        recorded = len(
            [e for e in c.store.events() if e.kind == "researcher_search_target_reached"]
        )
        record_search_targets(c, {agent_id: 11, agents[1]: 4}, 10)
        assert len(
            [e for e in c.store.events() if e.kind == "researcher_search_target_reached"]
        ) == recorded

    asyncio.run(scenario())


def test_kernelbench_verification_uses_only_own_attempt_receipts(tmp_path: Path) -> None:
    import hashlib
    import runpy

    client = runpy.run_path("examples/kernelbench_artifacts/client.py")
    source = b"public candidate"
    artifact = tmp_path / "candidate/L3P10/model_new.py"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(source)
    jobs = {}
    evaluations = []
    for agent, score in [("my-attempt", 1.2), ("peer", 50.0)]:
        jobs[agent] = {"agent_id": agent}
        output = tmp_path / "execution-outputs" / agent
        output.mkdir(parents=True)
        (output / "remote-job.json").write_text(json.dumps({"id": agent}))
        evaluations.append(
            {
                "id": agent,
                "kind": "eval",
                "state": "succeeded",
                "correct": True,
                "speedup": score,
                "source_sha256": hashlib.sha256(source).hexdigest(),
                "evaluation": {"evaluation_policy": "strict-fp32-v1", "seed": 42},
            }
        )
    (tmp_path / "coordination").mkdir()
    (tmp_path / "coordination/jobs.json").write_text(json.dumps(jobs))
    scoped = client["attempt_status"](
        {"L3P10": {"jobs": evaluations, "used": 2}}, tmp_path, "my-attempt"
    )
    score, metrics, evidence = client["score_bundle"](
        {"tasks": {"L3P10": {}}, "metric": "speedup", "exploration_seed": 42},
        artifact.parents[1],
        scoped,
        "search",
    )
    assert score == 1.2 and metrics["L3P10_submissions"] == 1
    assert evidence["L3P10"]["matching_evaluations"] == 1
    assert "peer" not in json.dumps(evidence)


def test_leader_budget_preserves_native_model_input_contract(tmp_path: Path) -> None:
    async def scenario() -> None:
        c, actions, agents = await setup(tmp_path, 1)
        c.state["runtime_turns"] = 7
        # Keep native-turn diagnostics, without adding them to model-visible budgets.
        assert c.summary()["runtime_turns"] == 7
        for agent in ["rsi", *agents]:
            assert "runtime_turns" not in actions.state(agent)["budget"]

    asyncio.run(scenario())
