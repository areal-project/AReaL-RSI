"""Persona libraries reach the same Runtime instruction path and survive recovery."""

import asyncio
from pathlib import Path
from typing import Any

from test_passk import ScriptedRuntime, configuration

from areal_rsi.coordination.collaboration import build_plan
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.coordination.engine import ToolSet
from areal_rsi.coordination.passk import PassKActions, launch
from areal_rsi.coordination.profile_resources import load_resources, prepare_profiles
from areal_rsi.modules.agent_runtime import TurnResult

ROOT = Path(__file__).resolve().parents[1]
BASE = ("leader_guidance", "problem_sharing", "researcher_profiles")


def test_libraries_reach_runtime_and_workspace_without_expanding_recovery(tmp_path: Path) -> None:
    class Capture(ScriptedRuntime):
        def __init__(self) -> None:
            super().__init__()
            self.requests: list[dict[str, Any]] = []

        async def turn(self, session_dir: Path, **kwargs: Any) -> TurnResult:
            self.requests.append(kwargs)
            return TurnResult("fixture", "turn", "", "completed", {})

    async def scenario() -> None:
        profiles = []
        for name in ("general", "algorithm", "memory", "execution"):
            directory = (
                ROOT / "src/areal_rsi/agents/research/resources/general"
                if name == "general"
                else ROOT / "examples/kernelbench_artifacts/v0.2.1.4/profiles" / name
            )
            profiles.append(
                {
                    "id": name,
                    "name": name,
                    "prompt": "replaced by the canonical AGENTS.md",
                    "reminder": name,
                    **load_resources(directory),
                }
            )
        profiles = prepare_profiles(profiles)
        c = Coordinator.create(tmp_path, configuration(4).brief)
        c.state["mode"] = "passk"
        c.start()
        plan = build_plan("passk", (*BASE, "profile_resources", "team_review"))
        actions = plan.actions(c)
        assert isinstance(actions, PassKActions)
        actions.researcher_profiles = tuple(profiles)
        await launch(c, actions)
        runtime = Capture()
        engine = plan.engine(
            c,
            runtime,
            lambda agent: ToolSet(actions.definitions(agent), actions.handler(agent)),
            research_prompt=plan.research_prompt,
            rsi_prompt=plan.rsi_prompt,
        )
        assert actions.definitions("rsi") == build_plan("passk", BASE).actions(c).definitions("rsi")
        for actor, profile in zip(c.store.run().research_agents, profiles, strict=True):
            for path, body in profile["resources"].items():
                assert (actions.workspace(actor.agent_id) / "researcher" / path).read_text() == body
            skills = [p for p in profile["resources"] if p.endswith("/SKILL.md")]
            assert len(skills) == (1 if profile["id"] == "execution" else 0)
            await engine._drive(actor.agent_id)
            call = runtime.requests[-1]
            for path in profile["required_reading"]:
                assert profile["resources"][path] in call["system_prompt"]
            assert "resources" not in call["recovery_state"]["researcher_profile"]
            assert "独立完成" in call["user_message"]
        restored = Coordinator(c.store)
        assert restored.state["researcher_profiles"] == c.state["researcher_profiles"]
        summary = restored.summary()["researcher_profiles"]
        assert all("resources" not in profile for profile in summary.values())
        assert prepare_profiles(profiles) == profiles

    asyncio.run(scenario())
