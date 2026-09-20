"""A silent researcher cannot park the leader; observation is not a trusted score."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from test_passk import ScriptedRuntime, setup

from areal_rsi.coordination.continuous_guidance import (
    ContinuousGuidanceActions,
    ContinuousGuidanceEngine,
)
from areal_rsi.coordination.engine import ToolSet
from areal_rsi.modules.agent_runtime import ToolCall, TurnResult


def test_leader_observes_without_reports_and_respects_pause_stop(tmp_path: Path) -> None:
    class Runtime(ScriptedRuntime):
        async def turn(self, session_dir: Path, **kwargs: Any) -> TurnResult:
            agent = kwargs["recovery_state"]["agent_id"]
            self.calls.append(agent)
            if agent == "rsi":
                await kwargs["handler"](ToolCall("get_state", {}, f"observe-{len(self.calls)}"))
                return TurnResult(session_dir.name, "observed", "继续核查", "completed", {})
            await kwargs["event_sink"](
                {"type": "model_output", "text": "发现此前的训练模式判断错了"}
            )
            await asyncio.Event().wait()
            raise AssertionError("cancelled")

    async def scenario() -> None:
        c, _, agents = await setup(tmp_path, 1)
        actions = ContinuousGuidanceActions(c)
        runtime = Runtime()
        engine = ContinuousGuidanceEngine(
            c, runtime, lambda a: ToolSet(actions.definitions(a), actions.handler(a))
        )
        try:
            async with asyncio.timeout(3):
                while runtime.calls.count("rsi") < 3:
                    await engine.tick()
                    await asyncio.sleep(0.01)
            researcher = actions.state("rsi")["researchers"][0]
            assert researcher["latest_report"] is None and researcher["best"] is None
            progress = researcher["recent_progress"][0]
            assert "训练模式" in actions.read_record("rsi", progress["uri"])["text"]
            assert not list((c.store.root / "reports").glob("*.json"))
            denied = await actions.handler("rsi")(ToolCall("wait", {"reason": "idle"}, "wait"))
            assert "error" in denied.output and not denied.stop
            guidance = await actions.handler("rsi")(
                ToolCall(
                    "advise_researcher",
                    {
                        "agent_id": agents[0],
                        "message": "核对实际调用路径",
                        "references": [progress["uri"]],
                    },
                    "guide",
                )
            )
            assert not actions.state("rsi")["researchers"][0]["latest_advice"]["acknowledged"]
            c.receive_during_turn(agents[0], guidance.output["message_id"])
            assert actions.state("rsi")["researchers"][0]["latest_advice"]["acknowledged"]
            c.pause()
            await engine.tick()
            count = runtime.calls.count("rsi")
            for _ in range(3):
                await engine.tick()
            assert runtime.calls.count("rsi") == count
            c.stop("finished")
            await engine.tick()
            assert not engine.active_agents
        finally:
            await engine.close()

    asyncio.run(scenario())
