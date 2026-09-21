"""Identity stays with a complete attempt, including session reconstruction."""

import asyncio
from pathlib import Path

import pytest
from test_passk import ScriptedRuntime, configuration

from areal_rsi.coordination.collaboration import build_plan
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.coordination.engine import ToolSet
from areal_rsi.coordination.passk import launch
from areal_rsi.modules.agent_runtime import TurnResult
from areal_rsi.research_app import ResearchApp


@pytest.mark.parametrize("extra", [(), ("objective_sharing",)])
def test_profiles_reach_runtime_and_recovery_without_changing_assignment(
    tmp_path: Path, extra: tuple[str, ...]
) -> None:
    class Capture(ScriptedRuntime):
        async def turn(self, session_dir, **kwargs):
            self.calls.append(kwargs)
            return TurnResult("fixture", "turn", "", "completed", {})

    async def scenario():
        c = Coordinator.create(tmp_path, configuration(4).brief)
        c.state["mode"] = "passk"
        c.start()
        plan = build_plan(
            "passk", ("leader_guidance", "problem_sharing", "researcher_profiles", *extra)
        )
        actions = plan.actions(c)
        actions.researcher_profiles = tuple(
            {
                "id": str(i),
                "name": f"Researcher preference {i}",
                "prompt": f"UNIQUE_PROFILE_{i}",
                "reminder": f"Prefer method {i}",
            }
            for i in range(4)
        )
        await launch(c, actions)
        runtime = Capture()
        engine = plan.engine(
            c,
            runtime,
            lambda agent: ToolSet(actions.definitions(agent), actions.handler(agent)),
            research_prompt=plan.research_prompt,
            rsi_prompt=plan.rsi_prompt,
        )
        for actor in c.store.run().research_agents:
            await engine._drive(actor.agent_id)
        assert len(runtime.calls) == 4
        for i, call in enumerate(runtime.calls):
            assert call["system_prompt"].index(f"UNIQUE_PROFILE_{i}") < call["system_prompt"].index(
                "首次通过正确性"
            )
            assert call["recovery_state"]["researcher_profile"]["id"] == str(i)
            assert "独立完成" in call["user_message"]
        recovered = Coordinator(c.store)
        assert recovered.state["researcher_profiles"] == c.state["researcher_profiles"]
        assert [r["profile"] for r in actions.state("rsi")["researchers"]] == [
            f"Researcher preference {i}" for i in range(4)
        ]

    asyncio.run(scenario())


def test_concurrency_limit_counts_finalization(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("RSI_MAX_CONCURRENT_RESEARCH", "3")
    app = ResearchApp(tmp_path)
    for i in range(3):
        c = Coordinator.create(tmp_path, configuration().brief)
        c.state["status"] = "running" if i < 2 else "stopped"
        if i == 2:
            c.state["finalization"] = {"status": "running"}
        c.save()
    next_run = Coordinator.create(tmp_path, configuration().brief)
    with pytest.raises(ValueError, match="上限为 3"):
        asyncio.run(app.start(next_run))
