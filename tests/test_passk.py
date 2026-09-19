"""Pass@k independence, sustained attempts, trusted stopping and recovery."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from areal_rsi.api.base import AgentState
from areal_rsi.api.research import ResearchLimits, TaskBrief, VerificationContract, VerifiedResult
from areal_rsi.base.storage import encode, now
from areal_rsi.coordination.config import ResearchConfig
from areal_rsi.coordination.coordinator import Coordinator, effect_id
from areal_rsi.coordination.engine import ToolSet
from areal_rsi.coordination.jobs import ResearchExecution
from areal_rsi.coordination.passk import PassKActions, PassKEngine, launch, stop_on_target
from areal_rsi.execution import ApprovedCommand
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
                max_model_calls=None,
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
    for name in ("max_model_calls", "max_total_tokens", "max_verify_calls"):
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
