"""Human instructions preserve attempt isolation and versioned result eligibility."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from runtime_fakes import ActionRuntime

from areal_rsi.api.interfaces import ToolCall
from areal_rsi.api.records import AgentState
from areal_rsi.api.research import (
    GoalRevision,
    ResearchLimits,
    ResearchMessage,
    TaskBrief,
    VerificationContract,
    VerifiedResult,
)
from areal_rsi.infra.coordination.config import ResearchConfig
from areal_rsi.infra.coordination.policies.passk import PassKActions, launch, stop_on_target
from areal_rsi.infra.execution import ApprovedCommand
from areal_rsi.infra.research import ResearchSystem
from areal_rsi.infra.storage.files import now
from areal_rsi.ui.workbench import public_detail


async def start(root: Path) -> tuple[ResearchSystem, str, list[str]]:
    config = ResearchConfig(TaskBrief("Write a greeting", "Keep it short",
                            ResearchLimits(max_agents=2),
                            VerificationContract("correct", target=1)))
    app = ResearchSystem(root, config=config, runtime_factory=lambda _: ActionRuntime())
    detail = await app.create({"conversation": False})
    run_id = detail["run_id"]
    await app.start(run_id)
    active = app.active[run_id]
    assert isinstance(active.actions, PassKActions)
    await launch(active.coordinator, active.actions)
    await active.engine.tick()
    await asyncio.sleep(.01)
    return app, run_id, [a.agent_id for a in active.coordinator.store.run().research_agents]


def test_guidance_is_durable_scoped_and_individually_paused(tmp_path: Path) -> None:
    async def scenario() -> None:
        app, run_id, agents = await start(tmp_path)
        try:
            c = app.coordinator(run_id)
            active = app.active[run_id]
            assert isinstance(active.actions, PassKActions)
            await app.set_agent_paused(run_id, agents[0], True)
            assert c.actor(agents[0]).state == AgentState.PAUSED
            assert agents[0] not in active.engine.active_agents
            assert agents[1] in active.engine.active_agents
            message = await app.send_message(run_id, "Use Chinese", recipient=agents[0],
                                             message_id="one-guidance")
            repeated = await app.send_message(run_id, "Use Chinese", recipient=agents[0],
                                              message_id="one-guidance")
            assert message == repeated
            with pytest.raises(ValueError, match="接收者"):
                await app.send_message(run_id, "bad", recipient="research_other_run")
            with pytest.raises(ValueError, match="idempotency"):
                await app.send_message(run_id, "different", recipient=agents[0],
                                       message_id="one-guidance")
            assert len(active.actions.state(agents[0])["human_guidance"]) == 1
            assert active.actions.state(agents[1])["human_guidance"] == []
            for _ in range(3):
                await active.engine.tick()
            assert agents[0] not in active.engine.active_agents
            detail = public_detail(await app.detail(run_id))
            own = next(m for m in detail["messages"] if m["message_id"] == message["message_id"])
            assert own["delivery_status"] == "pending"
            assert detail["human"]["assisted"]
            await app.close()
            app = ResearchSystem(tmp_path, runtime_factory=lambda _: ActionRuntime())
            c = app.coordinator(run_id)
            assert c.state.human.paused_agents == [agents[0]]
            await app.set_agent_paused(run_id, agents[0], False)
            active = app.active[run_id]
            assert isinstance(active.actions, PassKActions)
            await asyncio.sleep(.01)
            assert agents[0] in active.engine.active_agents
            assert active.actions.state(agents[0])["human_guidance"][0]["text"] == "Use Chinese"
            await active.engine._event(agents[0], {"type": "message_injected",
                                                  "message_id": message["message_id"]})
            detail = public_detail(await app.detail(run_id))
            own = next(m for m in detail["messages"] if m["message_id"] == message["message_id"])
            assert own["delivery_status"] == "delivered"
            await app.pause(run_id)
            await app.set_agent_paused(run_id, agents[0], True)
            await app.resume(run_id)
            await asyncio.sleep(.01)
            assert agents[0] not in app.active[run_id].engine.active_agents
        finally:
            await app.close()
    asyncio.run(scenario())


def test_goal_revision_excludes_old_results_and_survives_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        app, run_id, agents = await start(tmp_path)
        try:
            c = app.coordinator(run_id)
            active = app.active[run_id]
            assert isinstance(active.actions, PassKActions)
            for agent in agents:
                (active.actions.workspace(agent) / "greeting.txt").write_text(agent)
            candidate = await c.store.snapshot(active.actions.workspace(agents[0]),
                                                agent_id=agents[0], description="old goal")
            result = VerifiedResult("old-score", candidate.candidate_id, c.store.contract_digest(),
                                    now(), "succeeded", score=.8, passed=True)
            c.store.record_verification(result)
            c.store.consider_best(result.result_id)
            await app.set_agent_paused(run_id, agents[0], True)
            # A command receipt from the old goal remains old even if read after switching.
            active.execution.commands["probe"] = ApprovedCommand("probe", ("true",))
            job = await active.execution.submit(agents[1], ToolCall("run_job", {
                "command_name": "probe", "parameters_json": "{}"}, "old-job"))
            await app.revise_goal(run_id, "Write a bilingual greeting",
                                  "Include English and Chinese",
                                  "revision-one", 0)
            assert c.store.brief().revision == 1
            assert c.store.run().best_result_ref is None
            assert not c.store.consider_best(result.result_id)
            assert active.actions.state(agents[0])["best"] is None
            assert active.execution.jobs[job.output["job_id"]].goal_revision == 0
            stop_on_target(c)
            assert c.state.status == "running"
            assert agents[0] in c.state.human.paused_agents
            for agent in agents:
                assert (active.actions.workspace(agent) / "greeting.txt").read_text() == agent
                guidance = active.actions.state(agent)["human_guidance"]
                assert "bilingual" in guidance[-1]["text"]
            messages = len(c.store.records("messages", ResearchMessage))
            await app.revise_goal(run_id, "Write a bilingual greeting",
                                  "Include English and Chinese",
                                  "revision-one", 0)
            assert len(c.store.records("messages", ResearchMessage)) == messages
            with pytest.raises(ValueError, match="其他页面"):
                await app.revise_goal(run_id, "stale edit", "", "stale-request", 0)
            with pytest.raises(ValueError, match="不同内容"):
                await app.revise_goal(run_id, "different", "", "revision-one", 0)
            await app.close()
            app = ResearchSystem(tmp_path, runtime_factory=lambda _: ActionRuntime())
            c = app.coordinator(run_id)
            assert app.config(c).brief.revision == 1
            assert c.state.human.goal_changes[0].previous.objective == "Write a greeting"
            assert c.state.human.goal_changes[0].applied
            detail = public_detail(await app.detail(run_id))
            assert detail["brief"]["revision"] == 1
            assert detail["human"]["goal_changes"][0]["brief"]["revision"] == 1
        finally:
            await app.close()
    asyncio.run(scenario())


def test_interrupted_goal_change_resumes_from_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        app, run_id, _ = await start(tmp_path)
        try:
            def fail(*_: object) -> None:
                raise OSError("interrupted write")
            monkeypatch.setattr(app, "_apply_goal_change", fail)
            with pytest.raises(OSError, match="interrupted"):
                await app.revise_goal(run_id, "Revised goal", "New requirements", "revision", 0)
            assert app.coordinator(run_id).state.status == "paused"
            await app.close()
            app = ResearchSystem(tmp_path, runtime_factory=lambda _: ActionRuntime())
            await app.resume(run_id)
            c = app.coordinator(run_id)
            assert c.store.brief().objective == "Revised goal"
            assert c.state.status == "running"
            assert c.state.human.goal_changes[0].applied
        finally:
            await app.close()
    asyncio.run(scenario())


def test_late_success_keeps_its_original_goal_revision(tmp_path: Path) -> None:
    from dataclasses import replace

    from areal_rsi.infra.coordination.coordinator import Coordinator
    from areal_rsi.infra.coordination.evaluation.jobs import ResearchExecution

    async def scenario() -> None:
        c = Coordinator.create(tmp_path, TaskBrief("Old", "Old",
                               verification=VerificationContract("score", target=1)))
        c.state.mode = "passk"
        c.start()
        actions = PassKActions(c)
        await launch(c, actions)
        agent = c.store.run().research_agents[0].agent_id
        (actions.workspace(agent) / "result.txt").write_text("old artifact")
        candidate = await c.store.snapshot(actions.workspace(agent), agent_id=agent,
                                            description="old artifact")
        runner = ResearchExecution(
            actions, verifier=ApprovedCommand("verify", ("true",)), simulated=True,
            mock_duration=.01,
            read_verification=lambda record, candidate, phase: VerifiedResult(
                record.job_id, candidate.candidate_id, c.store.contract_digest(), now(),
                "succeeded", score=1, passed=True, simulated=True),
        )
        try:
            submitted = await runner.submit(agent, ToolCall("verify_candidate", {
                "candidate_id": candidate.candidate_id, "phase": "search"}, "old-check"))
            identifier = submitted.output["job_id"]
            async with asyncio.timeout(3):
                while not runner.manager.get(identifier).terminal:
                    runner.manager.poll()
                    await asyncio.sleep(.02)
            old_brief = c.store.brief()
            run = c.store.run()
            run.brief_ref = c.store.put("briefs", "new-goal",
                                       replace(c.store.brief(), objective="New", revision=1))
            c.store.save_run(run)
            result = runner._verification_result(runner.manager.get(identifier))
            assert result.passed and result.goal_revision == 0
            assert c.store.run().best_result_ref is None
            assert not c.store.consider_best(result.result_id, simulated_run=True)
            stop_on_target(c)
            assert c.state.status == "running"
            change = GoalRevision("revision", old_brief, c.store.brief(), now(), applied=True)
            c.state.human.goal_changes.append(change)
            current = replace(result, result_id="current-score", goal_revision=1)
            c.store.record_verification(current)
            c.store.consider_best(current.result_id, simulated_run=True)
            stop_on_target(c)
            assert c.state.status == "running"  # Fresh scoring alone does not cover a new goal.
            change.verification_confirmed = True
            stop_on_target(c)
            assert c.state.status == "succeeded"
        finally:
            runner.close(cancel=True)
    asyncio.run(scenario())
