"""Identity stays with a complete attempt, including session reconstruction."""

import asyncio
import json
from pathlib import Path

import pytest
from test_passk import ScriptedRuntime, configuration

from areal_rsi.coordination.collaboration import build_plan
from areal_rsi.coordination.config import load_config
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.coordination.engine import ToolSet
from areal_rsi.coordination.passk import launch
from areal_rsi.modules.agent_runtime import TurnResult
from areal_rsi.research_app import ResearchApp
from areal_rsi.researcher_profiles import load_profiles


def test_selected_assets_are_frozen_before_creation_and_survive_removal(tmp_path: Path) -> None:
    async def scenario() -> None:
        directory = tmp_path / "profiles" / "mybench"
        directory.mkdir(parents=True)
        asset = directory / "structure.md"
        asset.write_text("---\nname: Structure\nreminder: Check structure\n---\nOriginal prompt\n")
        config = configuration(2)
        config.collaboration_modules = ("leader_guidance", "problem_sharing", "researcher_profiles")
        config.researcher_profile_ids = ("mybench/structure", "general")
        config.researcher_profile_dir = "profiles"
        path = tmp_path / "research.json"
        path.write_text(json.dumps(config.public()))
        app = ResearchApp(tmp_path / "data", config=load_config(path))
        created = await app.create({})
        c = app.coordinator(created["run_id"])
        asset.unlink()
        restored = app.config(c)
        profiles = restored.metadata["researcher_profiles"]
        assert [p["id"] for p in profiles] == ["mybench/structure", "general"]
        assert profiles[0]["prompt"] == "Original prompt\n"
        assert restored.researcher_profile_ids == config.researcher_profile_ids

    asyncio.run(scenario())


def test_profile_selection_rejects_missing_assets_and_wrong_team_size() -> None:
    with pytest.raises(ValueError, match="Unknown researcher profile"):
        load_profiles(["not-an-installed-profile"])
    config = configuration(4)
    config.collaboration_modules = ("leader_guidance", "problem_sharing", "researcher_profiles")
    config.researcher_profile_ids = ("general",)
    with pytest.raises(ValueError, match="one profile for each researcher"):
        config.freeze_researcher_profiles()


@pytest.mark.parametrize("extra", [(), ("objective_sharing",), ("researcher_skills",)])
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
        if "researcher_skills" in extra:
            from areal_rsi.coordination.researcher_skills import prepare_skills

            profiles = list(actions.researcher_profiles)
            profiles[-1]["resources"] = {"skills/example/SKILL.md": "EXCLUSIVE_SKILL"}
            actions.researcher_profiles = tuple(prepare_skills(profiles))
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
            if "researcher_skills" in extra:
                assert ("EXCLUSIVE_SKILL" in call["system_prompt"]) == (i == 3)
                actor = c.store.run().research_agents[i]
                path = actions.workspace(actor.agent_id) / "researcher/skills/example/SKILL.md"
                assert path.exists() == (i == 3)
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
