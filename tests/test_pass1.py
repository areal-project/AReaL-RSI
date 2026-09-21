"""Pass1 continuation and durable, paginated per-turn public context."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from runtime_fakes import CallbackRuntime

from areal_rsi.api.interfaces import ToolCall, TurnResult
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
            assert delivery["recovery_state"]["agent_id"] == "rsi"
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
            await app.create(
                {
                    "run_id": "goal",
                    "conversation": True,
                    "message": "检查算术任务",
                    "collaboration": {"mode": "pass1", "modules": []},
                }
            )
            async with asyncio.timeout(5):
                while (await app.detail("goal"))["goal"]["status"] != "blocked":
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
            assert older["goal_turns"][0]["inputs"][0]["text"] == "检查算术任务"
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
            await app.create(
                {
                    "run_id": "live",
                    "conversation": True,
                    "message": "检查代码",
                    "collaboration": {"mode": "pass1", "modules": []},
                }
            )
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
            assert deliveries == ["检查代码", "改为检查远程目录"]
            await app.cancel("live")
            view = await app.detail("live")
            assert view["status"] == "paused"
            assert view["goal_turns"][0]["status"] == "interrupted"
            assert app.coordinator("live").actor("rsi").wait_reason == "user_cancelled"
            assert not app.coordinator("live").state.active
        finally:
            await app.close()

    asyncio.run(scenario())
