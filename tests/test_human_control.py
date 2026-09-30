"""Human instructions preserve attempt isolation and versioned result eligibility."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from runtime_fakes import ActionRuntime, CallbackRuntime

from areal_rsi.api.interfaces import ToolCall, TurnResult
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
from areal_rsi.infra.coordination.coordinator import effect_id
from areal_rsi.infra.coordination.policies.passk import PassKActions, launch, stop_on_target
from areal_rsi.infra.execution import ApprovedCommand
from areal_rsi.infra.research import ResearchSystem
from areal_rsi.infra.storage.files import now
from areal_rsi.ui.workbench import public_detail


async def start(root: Path) -> tuple[ResearchSystem, str, list[str]]:
    config = ResearchConfig(TaskBrief("Write a greeting", "Keep it short",
                            ResearchLimits(max_agents=2),
                            VerificationContract("correct", target=1), acceptance=["English only"]))
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


def test_interaction_continues_after_research_stops_without_reopening_it(tmp_path: Path) -> None:
    async def scenario() -> None:
        turns = 0

        async def respond(session: Path, request: dict[str, Any]) -> TurnResult:
            nonlocal turns
            if session.name != "interaction":
                await asyncio.Event().wait()
                raise AssertionError("research turn should have been cancelled")
            turns += 1
            if turns == 1:
                result = await request["handler"](
                    ToolCall(
                        "ask_user", {"question": "还需要提交到哪个仓库？"}, "followup-question"
                    )
                )
                return TurnResult(session.name, request["message_id"], "", result.reason, {})
            return TurnResult(session.name, request["message_id"], "收到仓库信息", "completed", {})

        runtime = CallbackRuntime(respond)
        app = ResearchSystem(
            tmp_path,
            config=ResearchConfig(TaskBrief("Investigate", "Inspect only")),
            runtime_factory=lambda _: runtime,
        )
        try:
            await app.create({"run_id": "stopped-chat", "conversation": False})
            await app.start("stopped-chat")
            await app.stop("stopped-chat")
            c = app.coordinator("stopped-chat")
            finished_at = c.store.run().finished_at
            await app.send_message("stopped-chat", "请继续沟通交付", recipient="interaction")
            async with asyncio.timeout(5):
                while not (questions := c.user_questions()):
                    await asyncio.sleep(.01)
            question = questions[0].message_id
            await app.send_message("stopped-chat", "提交到指定仓库", reply_to=question)
            async with asyncio.timeout(5):
                while not any(
                    message.sender == "interaction" and message.text == "收到仓库信息"
                    for message in c.store.records("messages", ResearchMessage)
                ):
                    await asyncio.sleep(.01)
            assert c.state.status == "stopped"
            assert c.store.run().finished_at == finished_at
            assert not any(event.kind == "run_reopened" for event in c.store.events())
            assert all(actor.state == AgentState.STOPPED for actor in c.store.run().research_agents)
            with pytest.raises(ValueError, match="接收者"):
                await app.send_message("stopped-chat", "不要重启研究员", recipient="research_other")
        finally:
            await app.close()

    asyncio.run(scenario())


def test_terminal_chat_retry_repairs_an_unqueued_message(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = ResearchSystem(
            tmp_path,
            config=ResearchConfig(TaskBrief("Investigate", "Inspect only")),
            runtime_factory=lambda _: ActionRuntime(),
        )
        try:
            await app.create({"run_id": "retry-chat", "conversation": False})
            await app.stop("retry-chat")
            c = app.coordinator("retry-chat")
            key = "interrupted-terminal-chat"
            message_id = effect_id("message", key)
            c.store.put(
                "messages", message_id,
                ResearchMessage(message_id, "user", "interaction", "继续对话", now()),
            )
            assert message_id not in c.state.inboxes.get("interaction", [])
            assert c.user_message("继续对话", key=key, recipient="interaction") == message_id
            assert message_id in c.state.inboxes["interaction"]
            assert c.actor("interaction").state == AgentState.WAITING
        finally:
            await app.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("pause_research", [False, True])
def test_interaction_can_reply_during_research(
    tmp_path: Path, pause_research: bool
) -> None:
    async def scenario() -> None:
        async def respond(session: Path, request: dict[str, Any]) -> TurnResult:
            if session.name != "interaction":
                await asyncio.Event().wait()
                raise AssertionError("research turn should have been cancelled")
            return TurnResult(session.name, request["message_id"], "可以继续讨论", "completed", {})

        app = ResearchSystem(
            tmp_path,
            config=ResearchConfig(TaskBrief("Investigate", "Inspect only")),
            runtime_factory=lambda _: CallbackRuntime(respond),
        )
        try:
            await app.create({"run_id": "live-chat", "conversation": False})
            await app.start("live-chat")
            if pause_research:
                await app.pause("live-chat")
            await app.send_message("live-chat", "还有一个问题", recipient="interaction")
            c = app.coordinator("live-chat")
            async with asyncio.timeout(5):
                while not any(
                    message.sender == "interaction" and message.text == "可以继续讨论"
                    for message in c.store.records("messages", ResearchMessage)
                ):
                    await asyncio.sleep(.01)
            assert c.state.status == ("paused" if pause_research else "running")
        finally:
            await app.close()

    asyncio.run(scenario())


def test_terminal_interaction_can_import_a_selected_researcher_candidate(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        app, run_id, agents = await start(tmp_path)
        try:
            active = app.active[run_id]
            assert isinstance(active.actions, PassKActions)
            source = active.actions.workspace(agents[1])
            (source / "result.txt").write_text("second researcher's result")
            candidate = await active.coordinator.store.snapshot(
                source, agent_id=agents[1], description="handoff"
            )
            await app.stop(run_id)
            await app.send_message(run_id, "请处理已保存的候选", recipient="interaction")
            active = app.active[run_id]
            assert isinstance(active.actions, PassKActions)
            assert "import_candidate" in {
                tool["name"] for tool in active.actions.definitions("interaction")
            }
            result = await active.actions.handler("interaction")(
                ToolCall(
                    "import_candidate",
                    {"candidate_id": candidate.candidate_id, "destination": "handoff"},
                    "import-for-delivery",
                )
            )
            assert "error" not in result.output
            assert (active.actions.workspace("interaction") / "handoff/result.txt").read_text() == (
                "second researcher's result"
            )
            assert active.coordinator.state.status == "stopped"
        finally:
            await app.close()

    asyncio.run(scenario())


def test_terminal_interaction_reply_recovers_after_worker_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        async def respond(session: Path, request: dict[str, Any]) -> TurnResult:
            if session.name != "interaction":
                await asyncio.Event().wait()
                raise AssertionError("research turn should have been cancelled")
            return TurnResult(
                session.name, request["message_id"], "恢复后仍可回复", "completed", {}
            )

        config = ResearchConfig(TaskBrief("Investigate", "Inspect only"))
        app = ResearchSystem(
            tmp_path, config=config, runtime_factory=lambda _: CallbackRuntime(respond)
        )
        await app.create({"run_id": "recover-chat", "conversation": False})
        await app.start("recover-chat")
        await app.stop("recover-chat")
        c = app.coordinator("recover-chat")
        c.acquire()
        try:
            c.user_message("继续沟通", key="queued-after-stop", recipient="interaction")
        finally:
            c.release()
        await app.close()

        recovered = ResearchSystem(
            tmp_path, runtime_factory=lambda _: CallbackRuntime(respond)
        )
        try:
            await recovered.recover()
            c = recovered.coordinator("recover-chat")
            async with asyncio.timeout(5):
                while not any(
                    message.sender == "interaction" and message.text == "恢复后仍可回复"
                    for message in c.store.records("messages", ResearchMessage)
                ):
                    await asyncio.sleep(.01)
            assert c.state.status == "stopped"
        finally:
            await recovered.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("recover", [False, True])
def test_terminal_chat_keeps_pending_finalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recover: bool
) -> None:
    async def scenario() -> None:
        async def respond(session: Path, request: dict[str, Any]) -> TurnResult:
            if session.name != "interaction":
                raise AssertionError("terminal chat must not restart researchers")
            return TurnResult(session.name, request["message_id"], "收到", "completed", {})

        config = ResearchConfig(
            TaskBrief("Investigate", "Inspect only"),
            finalizer=ApprovedCommand("finish", ("true",)),
        )
        app = ResearchSystem(
            tmp_path, config=config, runtime_factory=lambda _: CallbackRuntime(respond)
        )
        try:
            await app.create({"run_id": "closeout-chat", "conversation": False})
            c = app.coordinator("closeout-chat")
            c.acquire()
            try:
                c.start()
                c.stop("Exploration budget ended")
            finally:
                c.release()
            called = asyncio.Event()

            async def finish(*_: Any) -> None:
                called.set()

            if recover:
                c.acquire()
                try:
                    c.user_message("继续沟通", key="queued-closeout", recipient="interaction")
                finally:
                    c.release()
                await app.close()
                app = ResearchSystem(
                    tmp_path, runtime_factory=lambda _: CallbackRuntime(respond)
                )
            monkeypatch.setattr(app, "_complete_result", finish)
            if recover:
                await app.recover()
            else:
                await app.send_message("closeout-chat", "继续沟通", recipient="interaction")
            await asyncio.wait_for(called.wait(), timeout=5)
            assert c.state.status == "stopped"
            assert any(
                message.sender == "interaction" and message.text == "收到"
                for message in c.store.records("messages", ResearchMessage)
            )
        finally:
            await app.close()

    asyncio.run(scenario())


def test_terminal_chat_queued_during_finalization_is_delivered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        async def respond(session: Path, request: dict[str, Any]) -> TurnResult:
            if session.name != "interaction":
                raise AssertionError("terminal chat must not restart researchers")
            return TurnResult(session.name, request["message_id"], "收尾后回复", "completed", {})

        app = ResearchSystem(
            tmp_path,
            config=ResearchConfig(
                TaskBrief("Investigate", "Inspect only"),
                finalizer=ApprovedCommand("finish", ("true",)),
            ),
            runtime_factory=lambda _: CallbackRuntime(respond),
        )
        entered, release = asyncio.Event(), asyncio.Event()
        try:
            await app.create({"run_id": "chat-during-finalization", "conversation": False})
            c = app.coordinator("chat-during-finalization")
            c.acquire()
            try:
                c.start()
                c.stop("Exploration budget ended")
            finally:
                c.release()

            async def finish(*_: Any) -> None:
                entered.set()
                await release.wait()

            monkeypatch.setattr(app, "_complete_result", finish)
            async with app.control("chat-during-finalization", "recover") as owned:
                await app.attach(owned)
            await asyncio.wait_for(entered.wait(), timeout=5)
            receipt = await app.send_message(
                "chat-during-finalization", "收尾期间继续对话", recipient="interaction"
            )
            release.set()
            async with asyncio.timeout(5):
                while not any(
                    message.sender == "interaction"
                    and message.reply_to == receipt["message_id"]
                    for message in c.store.records("messages", ResearchMessage)
                ):
                    await asyncio.sleep(.01)
            assert c.state.status == "stopped"
        finally:
            release.set()
            await app.close()

    asyncio.run(scenario())


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
            await app.update_goal(run_id, "Write a bilingual greeting",
                                  "Include English and Chinese",
                                  "revision-one", 0, acceptance=["English and Chinese present"])
            assert c.store.brief().revision == 1
            assert c.store.brief().acceptance == ["English and Chinese present"]
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
                assert "English and Chinese present" in guidance[-1]["text"]
            messages = len(c.store.records("messages", ResearchMessage))
            await app.update_goal(run_id, "Write a bilingual greeting",
                                  "Include English and Chinese",
                                  "revision-one", 0, acceptance=["English and Chinese present"])
            assert len(c.store.records("messages", ResearchMessage)) == messages
            with pytest.raises(ValueError, match="不同内容"):
                await app.update_goal(run_id, "Write a bilingual greeting",
                                      "Include English and Chinese", "revision-one", 0,
                                      acceptance=["Different acceptance"])
            with pytest.raises(ValueError, match="Invalid update-goal"):
                await app.update_goal(run_id, "New", "", "empty-acceptance", 1,
                                      acceptance=[])
            with pytest.raises(ValueError, match="其他页面"):
                await app.update_goal(run_id, "stale edit", "", "stale-request", 0,
                                      acceptance=["New check"])
            with pytest.raises(ValueError, match="不同内容"):
                await app.update_goal(run_id, "different", "", "revision-one", 0,
                                      acceptance=["English and Chinese present"])
            await app.close()
            app = ResearchSystem(tmp_path, runtime_factory=lambda _: ActionRuntime())
            c = app.coordinator(run_id)
            assert app.config(c).brief.revision == 1
            assert app.config(c).brief.acceptance == ["English and Chinese present"]
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
                await app.update_goal(run_id, "Revised goal", "New requirements", "revision", 0,
                                      acceptance=["New check"])
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


@pytest.mark.parametrize("released", [True, False])
def test_cancellation_waits_for_terminal_job_resource_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, released: bool,
) -> None:
    from dataclasses import replace

    from areal_rsi.infra.coordination.coordinator import Coordinator
    from areal_rsi.infra.coordination.evaluation.jobs import ResearchExecution
    from areal_rsi.infra.execution import JobRecord, JobSpec

    async def scenario() -> None:
        c = Coordinator.create(tmp_path, TaskBrief("task", "task"))
        execution = ResearchExecution(PassKActions(c))
        record = JobRecord("probe", JobSpec(("true",), str(tmp_path)), "lost", 0, 0,
                           resources_released=False)
        polls = 0

        def poll() -> list[JobRecord]:
            nonlocal record, polls
            polls += 1
            if polls == 2 and released:
                record = replace(record, resources_released=True)
            return [record]

        try:
            with monkeypatch.context() as patch:
                patch.setattr(execution.manager, "records", lambda: [record])
                patch.setattr(execution.manager, "get", lambda _: record)
                patch.setattr(execution.manager, "cancel", lambda _: record)
                patch.setattr(execution.manager, "poll", poll)
                if released:
                    await execution.cancel_pending()
                    assert polls == 2 and record.resources_released
                else:
                    with pytest.raises(RuntimeError, match="resources remain reserved"):
                        await execution.cancel_pending()
                    assert not record.resources_released
        finally:
            execution.close()

    asyncio.run(scenario())
