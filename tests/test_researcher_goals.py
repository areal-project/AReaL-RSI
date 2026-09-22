"""Unified researcher continuation and durable, paginated per-turn public context."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from runtime_fakes import CallbackRuntime

from areal_rsi.api.interfaces import ToolCall, TurnResult
from areal_rsi.api.research import ResearchLimits, TaskBrief
from areal_rsi.infra.coordination.config import ResearchConfig
from areal_rsi.infra.research import ResearchSystem
from areal_rsi.ui.workbench import public_detail


def progress(status: str, index: int) -> dict[str, Any]:
    return {
        "status": status,
        "summary": f"进展 {index}",
        "current_step": "验收",
        "next_steps": ["执行下一步"] if status == "active" else [],
        "acceptance": ["输出 42"],
        "evidence": ["运行输出 42"] if status == "completed" else [],
        "blocker": "需要用户补充条件" if status == "blocked" else "",
        "candidate_id": "",
    }


def test_turn_history_continues_blocks_and_survives_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        count = 0

        async def respond(session: Path, delivery: dict[str, Any]) -> TurnResult:
            nonlocal count
            index = count
            count += 1
            assert delivery["recovery_state"]["agent_id"].startswith("research_")
            await delivery["event_sink"]({"type": "model_output", "text": f"本轮执行说明 {index}"})
            await delivery["event_sink"]({"type": "tool_started", "name": "inspect",
                                          "call_id": "inspect", "arguments": "file.py"})
            args = progress(("active", "blocked", "completed")[index], index)
            result = await delivery["handler"](ToolCall("update_goal", args, f"progress-{index}"))
            assert "error" not in result.output
            await delivery["event_sink"]({"type": "tool_completed", "name": "inspect",
                                          "call_id": "inspect", "result": "42"})
            if index == 2:
                await (
                    asyncio.Event().wait()
                )  # Goal completion may cancel a native response in flight.
            return TurnResult(
                session.name, str(index), "这一轮完成", result.reason or "completed", {}
            )

        def system() -> ResearchSystem:
            return ResearchSystem(tmp_path, runtime_factory=lambda _: CallbackRuntime(respond))

        app = system()
        try:
            config = ResearchConfig(TaskBrief("检查算术任务", "检查算术任务",
                                             ResearchLimits(max_agents=1)))
            await app.create({"run_id": "goal", "config": config.public()})
            await app.start("goal")
            async with asyncio.timeout(5):
                while ((await app.detail("goal"))["goal"] or {}).get("status") != "blocked":
                    await asyncio.sleep(0.01)
            await asyncio.sleep(0.1)
            assert count == 2, "Blocked goals must not spin"
            view = public_detail(await app.detail("goal", goal_limit=1))
            turn = view["goal_turns"][0]
            assert turn["number"] == 2 and turn["status"] == "blocked"
            assert turn["context_before"]["summary"] == "进展 0"
            assert turn["context_after"]["summary"] == "进展 1"
            assert turn["output"] == ["本轮执行说明 1"]
            assert "tools" not in turn
            assert turn["last_tool"] is None
            assert view["goal_turns_next"] == 2
            older = await app.detail("goal", goal_before=2, goal_limit=1)
            assert "检查算术任务" in older["goal_turns"][0]["inputs"][0]["text"]
            assert older["goal_turns_next"] is None
            original = (tmp_path / "runs/goal/goal-turns/00000001.json").read_bytes()
            await app.detail("goal")
            assert (tmp_path / "runs/goal/goal-turns/00000001.json").read_bytes() == original
        finally:
            await app.close()
        app = system()
        try:
            await app.send_message("goal", "条件已补充，请继续")
            async with asyncio.timeout(5):
                while (await app.detail("goal"))["worker_alive"]:
                    await asyncio.sleep(0.01)
            view = public_detail(await app.detail("goal"))
            assert view["status"] == "succeeded"
            assert [turn["number"] for turn in view["goal_turns"]] == [3, 2, 1]
            assert view["goal_turns"][0]["inputs"][0]["text"] == "条件已补充，请继续"
            assert view["goal_turns"][0]["status"] == "completed"
            assert view["goal_turns"][1]["context_after"]["status"] == "blocked"
            assert not app.coordinator("goal").state.active
            with pytest.raises(ValueError, match="cannot be resumed"):
                await app.resume("goal")
        finally:
            await app.close()

    asyncio.run(scenario())


def test_live_round_records_interruption(tmp_path: Path) -> None:
    async def scenario() -> None:
        entered = asyncio.Event()
        deliveries: list[str] = []

        async def respond(session: Path, delivery: dict[str, Any]) -> TurnResult:
            deliveries.append(delivery["user_message"])
            await delivery["event_sink"]({"type": "model_output", "text": "正在检查文件"})
            await delivery["event_sink"]({"type": "tool_started", "name": "read_file"})
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        app = ResearchSystem(tmp_path, runtime_factory=lambda _: CallbackRuntime(respond))
        try:
            config = ResearchConfig(TaskBrief("检查代码", "检查代码", ResearchLimits(max_agents=1)))
            await app.create({"run_id": "live", "config": config.public()})
            await app.start("live")
            await asyncio.wait_for(entered.wait(), 3)
            live = public_detail(await app.detail("live"))["goal_turns"][0]
            assert live["status"] == "running" and live["tool_running"]
            assert live["output"] == ["正在检查文件"]
            await app.pause("live")
            ended = public_detail(await app.detail("live"))["goal_turns"][0]
            assert ended["status"] == "interrupted" and ended["finished_at"]
            assert not ended["tool_running"]
            assert app.coordinator("live").state.active, "Pause retains the active delivery"
            await app.call("cancel", {"run_id": "live"})
            assert not app.coordinator("live").state.active
            assert (await app.detail("live"))["status"] == "paused"
            await asyncio.sleep(0.05)
            assert len(deliveries) == 1, "Cancellation must not auto-continue"
            entered.clear()
            await app.send_message("live", "改为检查远程目录")
            await asyncio.wait_for(entered.wait(), 3)
            assert "检查代码" in deliveries[0]
            assert deliveries[1] == "改为检查远程目录"
            await app.cancel("live")
            view = await app.detail("live")
            assert view["status"] == "paused"
            assert view["goal_turns"][0]["status"] == "interrupted"
            assert all(a.wait_reason == "user_cancelled"
                       for a in app.coordinator("live").store.run().research_agents)
            assert not app.coordinator("live").state.active
            entered.clear()
            await app.resume("live")
            await asyncio.wait_for(entered.wait(), 3)
            assert len(deliveries) == 3
            assert "用户已恢复执行" in deliveries[-1]
        finally:
            await app.close()

    asyncio.run(scenario())


def test_two_attempts_keep_completion_and_blocking_separate(tmp_path: Path) -> None:
    async def scenario() -> None:
        calls: dict[str, int] = {}
        blocked_agent = ""

        async def respond(session: Path, delivery: dict[str, Any]) -> TurnResult:
            nonlocal blocked_agent
            agent_id = delivery["recovery_state"]["agent_id"]
            assert agent_id not in {"rsi", "interaction"}, "Code RSI must not call a model"
            calls[agent_id] = calls.get(agent_id, 0) + 1
            if not blocked_agent:
                blocked_agent = agent_id
            state = "blocked" if agent_id == blocked_agent and calls[agent_id] == 1 else "completed"
            await delivery["event_sink"]({"type": "model_output", "text": agent_id})
            result = await delivery["handler"](ToolCall("update_goal", progress(state, 1),
                                                        agent_id + str(calls[agent_id])))
            assert "error" not in result.output
            return TurnResult(session.name, delivery["message_id"], state, result.reason, {})

        app = ResearchSystem(tmp_path, runtime_factory=lambda _: CallbackRuntime(respond))
        try:
            config = ResearchConfig(TaskBrief("Check 42", "Independent attempts",
                                             ResearchLimits(max_agents=2)))
            await app.create({"run_id": "pair", "config": config.public()})
            await app.start("pair")
            async with asyncio.timeout(5):
                while len(calls) != 2:
                    await asyncio.sleep(.01)
            await asyncio.sleep(.1)
            c = app.coordinator("pair")
            assert c.state.status == "running"
            assert c.actor("rsi").wait_reason == "code_coordination"
            statuses = sorted(goal.status for goal in c.state.goals.values())
            assert statuses == ["blocked", "completed"]
            assert list(calls.values()) == [1, 1]
            detail = public_detail(await app.detail("pair"))
            assert {t["agent_id"] for t in detail["goal_turns"]} == set(calls)
            assert {t["context_after"]["status"] for t in detail["goal_turns"]} == {
                "blocked", "completed"
            }
            await app.send_message("pair", "Conditions supplied", recipient=blocked_agent)
            async with asyncio.timeout(5):
                while (await app.detail("pair"))["status"] != "succeeded":
                    await asyncio.sleep(.01)
            assert calls[blocked_agent] == 2
            assert sum(calls.values()) == 3
            assert c.state.goals[blocked_agent].status == "completed"
        finally:
            await app.close()

    asyncio.run(scenario())


def test_researcher_completion_requires_own_verified_candidate(tmp_path: Path) -> None:
    from runtime_fakes import ActionRuntime

    from areal_rsi.api.research import VerificationContract, VerifiedResult
    from areal_rsi.infra.storage.files import now

    async def scenario() -> None:
        app = ResearchSystem(tmp_path, runtime_factory=lambda _: ActionRuntime())
        try:
            config = ResearchConfig(TaskBrief("Check artifact", "Require real verification",
                ResearchLimits(max_agents=2), VerificationContract("valid")))
            app.default_config = config
            await app.create({"run_id": "verified", "conversation": False})
            await app.start("verified")
            active = app.active["verified"]
            await active.engine.tick()
            await asyncio.sleep(.01)
            c = app.coordinator("verified")
            first, second = c.store.run().research_agents
            (active.actions.workspace(first.agent_id) / "result.txt").write_text("42")
            candidate = await c.store.snapshot(active.actions.workspace(first.agent_id),
                                               agent_id=first.agent_id, description="42")
            args = {**progress("completed", 1), "candidate_id": candidate.candidate_id}
            missing = await active.actions.handler(first.agent_id)(
                ToolCall("update_goal", args, "missing-verification"))
            assert "verification" in missing.output["error"]
            assert c.state.goals[first.agent_id].status == "active"
            c.store.record_verification(VerifiedResult("check", candidate.candidate_id,
                c.store.contract_digest(), now(), "succeeded", score=1, passed=True))
            wrong_owner = await active.actions.handler(second.agent_id)(
                ToolCall("update_goal", args, "wrong-owner"))
            assert "not part" in wrong_owner.output["error"]
            result = await active.actions.handler(first.agent_id)(
                ToolCall("update_goal", args, "verified-completion"))
            assert "error" not in result.output
            assert c.state.goals[first.agent_id].status == "completed"
            assert c.state.goals[second.agent_id].status == "active"
        finally:
            await app.close()

    asyncio.run(scenario())
