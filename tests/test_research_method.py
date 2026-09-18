"""Custom Research instructions remain fixed across recovery and role boundaries."""

import asyncio
from pathlib import Path

import pytest

from areal_rsi.api.research import TaskBrief
from areal_rsi.base.storage import decode, write_json
from areal_rsi.coordination.actions import ResearchActions
from areal_rsi.coordination.agent_config import AgentConfig, research_prompt, run_method
from areal_rsi.coordination.config import ResearchConfig, config_from_dict, load_config
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.modules.responses import ToolCall, ToolResult
from areal_rsi.research_app import ResearchApp


def experiment(root: Path) -> Path:
    root.mkdir()
    (root / "prompt.md").write_text("Custom research method.")
    (root / "skills/check").mkdir(parents=True)
    (root / "skills/check/SKILL.md").write_text("Inspect references.md before proposing.")
    (root / "skills/check/references.md").write_text("Compare observed outcomes.")
    path = root / "experiment.yaml"
    path.write_text(
        "brief:\n  objective: Test research\n  instructions: Inspect evidence\n"
        "agents:\n  research:\n    base: default\n    prompt: prompt.md\n    skills: skills\n"
    )
    return path


def test_snapshot_survives_source_changes_and_config_roundtrip(tmp_path: Path) -> None:
    path = experiment(tmp_path / "method")
    config = load_config(path)
    assert config.agents.research is not None
    assert config.agents.research.prompt == str(path.parent / "prompt.md")
    root = tmp_path / "run"
    write_json(root / "coordination/config.json", config.public())
    (path.parent / "prompt.md").unlink()
    (path.parent / "skills/check/references.md").write_text("Changed after launch")
    restored: ResearchConfig = decode(ResearchConfig, config.public())
    assert restored.agents.research is not None
    restored.agents.research.freeze()  # Recovery must never reopen the source.
    method = run_method(root)
    assert method is not None
    assert method.skills["check/references.md"] == "Compare observed outcomes."
    prompt = research_prompt("DEFAULT ROLE MUST NOT APPEAR", method)
    assert "DEFAULT ROLE" not in prompt
    assert "Custom research method." in prompt
    assert "check/SKILL.md" in prompt
    assert "Compare observed outcomes." not in prompt  # Read on demand.
    assert research_prompt("default", None) == "default"


def test_skill_tool_uses_frozen_files_and_is_research_only(tmp_path: Path) -> None:
    config = load_config(experiment(tmp_path / "method"))
    c = Coordinator.create(tmp_path / "data", TaskBrief("Inspect", "Report findings"))
    write_json(c.store.root / "coordination/config.json", config.public())
    c.start()
    agent = c.spawn(
        key="spawn", direction="Inspect", instruction="Check", completion_criteria="Report"
    )
    agent_id = agent["agent_id"]
    actions = ResearchActions(c)
    assert "read_agent_skill" in {tool["name"] for tool in actions.definitions(agent_id)}
    for role in ("rsi", "interaction"):
        assert "read_agent_skill" not in {tool["name"] for tool in actions.definitions(role)}

    async def read_skill(path: str, call_id: str) -> ToolResult:
        return await actions.handler(agent_id)(
            ToolCall("read_agent_skill", {"path": path}, call_id)
        )

    result = asyncio.run(read_skill("check/references.md", "read"))
    assert result.output["text"] == "Compare observed outcomes."
    invalid = asyncio.run(read_skill("../config.json", "escape"))
    assert "error" in invalid.output
    assert not (c.store.paths.agent(agent_id) / "workspace/skills").exists()


@pytest.mark.parametrize("problem", ["missing", "empty", "symlink", "large"])
def test_invalid_method_fails_at_load(tmp_path: Path, problem: str) -> None:
    path = experiment(tmp_path / "method")
    prompt = path.parent / "prompt.md"
    if problem == "missing":
        prompt.unlink()
    elif problem == "empty":
        prompt.write_text(" ")
    elif problem == "symlink":
        (path.parent / "skills/linked").symlink_to(prompt)
    else:
        prompt.write_text("x" * 1_000_001)
    with pytest.raises(ValueError):
        load_config(path)


def test_skills_only_and_snapshot_integrity(tmp_path: Path) -> None:
    config = load_config(experiment(tmp_path / "method"))
    assert config.agents.research is not None
    method = AgentConfig(skills=config.agents.research.skills)
    method.freeze()
    from areal_rsi.agents.research.prompt import PROMPT

    assert research_prompt("default", method.snapshot).startswith(PROMPT)
    assert method.snapshot is not None
    method.snapshot.skills["check/SKILL.md"] = "tampered"
    with pytest.raises(ValueError, match="digest"):
        method.freeze()


def test_app_creation_freezes_method_before_source_is_removed(tmp_path: Path) -> None:
    path = experiment(tmp_path / "method")
    config = load_config(path)
    assert config.agents.research is not None
    config.agents.research.snapshot = None  # Exercise programmatic config creation.

    async def scenario() -> None:
        app = ResearchApp(tmp_path / "data", config=config)
        created = await app.create({})
        run_id = created["run_id"]
        (path.parent / "prompt.md").unlink()
        restored_app = ResearchApp(tmp_path / "data")
        c = restored_app.coordinator(run_id)
        restored = restored_app.config(c)
        assert restored.agents.research is not None
        assert restored.agents.research.snapshot is not None
        assert restored.agents.research.snapshot.prompt == "Custom research method."
        assert run_method(c.store.root) == restored.agents.research.snapshot

    asyncio.run(scenario())


def test_prompt_override_preserves_other_configuration_and_resolves_from_yaml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = experiment(tmp_path / "method")
    path.write_text(
        "brief:\n  objective: Inspect\n  instructions: Check evidence\n"
        "agents:\n  research:\n    prompt: prompt.md\n"
    )
    monkeypatch.chdir(tmp_path)
    config = load_config(path)
    assert config.agents.research is not None
    assert config.agents.research.base == "default"
    assert config.agents.research.snapshot is not None
    assert config.agents.research.snapshot.prompt == "Custom research method."
    assert config.agents.research.snapshot.skills == {}
    actual = config.public()
    actual.pop("agents")
    default = ResearchConfig(config.brief).public()
    default.pop("agents")
    assert actual == default


@pytest.mark.parametrize(
    "agents",
    [
        None,
        {"interaction": {"prompt": "prompt.md"}},
        {"research": {"base": "unknown"}},
        {"research": {"harness": "custom.py"}},
        {"research": {"memory": "custom.py"}},
        {"research": {"mode": "append"}},
        {"research": {"prompt": 42}},
    ],
)
def test_unsupported_overrides_fail_explicitly(agents: object) -> None:
    with pytest.raises(ValueError):
        config_from_dict(
            {"brief": {"objective": "Test", "instructions": "Check"}, "agents": agents}
        )


def test_legacy_json_without_agents_remains_default(tmp_path: Path) -> None:
    path = tmp_path / "research.json"
    path.write_text('{"brief":{"objective":"Inspect","instructions":"Report"}}')
    config = load_config(path)
    assert config.agents.research is None


def test_documented_example_loads() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "experiments/research-example/experiment.yaml")
    assert config.agents.research is not None
    assert config.agents.research.snapshot is not None
    assert "evidence/SKILL.md" in config.agents.research.snapshot.skills
    assert config.agents.rsi is not None
    assert config.agents.rsi.snapshot is not None
    assert "dispatch/SKILL.md" in config.agents.rsi.snapshot.skills


def test_skills_override_preserves_main_prompt_variant(tmp_path: Path) -> None:
    from areal_rsi.coordination.collaboration import build_plan

    path = experiment(tmp_path / "method")
    path.write_text(
        "brief:\n  objective: Inspect\n  instructions: Report\n"
        "research_prompt_variant: detailed\n"
        "agents:\n  research:\n    skills: skills\n"
    )
    config = load_config(path)
    assert config.agents.research is not None
    assert config.agents.research.snapshot is not None
    expected = build_plan(config.mode, config.collaboration_modules, "detailed")
    assert config.agents.research.snapshot.prompt == expected.research_prompt


def test_passk_keeps_configured_skill_tools_for_both_roles(tmp_path: Path) -> None:
    from areal_rsi.coordination.passk import PassKActions

    config = load_config(experiment(tmp_path / "method"))
    config.agents.rsi = config.agents.research
    c = Coordinator.create(tmp_path / "data", TaskBrief("Inspect", "Report findings"))
    write_json(c.store.root / "coordination/config.json", config.public())
    c.start()
    agent = c.spawn(
        key="spawn", direction="Inspect", instruction="Check", completion_criteria="Report"
    )
    actions = PassKActions(c)
    for role in ("rsi", agent["agent_id"]):
        assert "read_agent_skill" in {tool["name"] for tool in actions.definitions(role)}


@pytest.mark.parametrize("role", ["research", "rsi"])
@pytest.mark.parametrize("component", ["prompt", "skills"])
def test_single_component_for_one_role_keeps_other_role_default(
    tmp_path: Path,
    role: str,
    component: str,
) -> None:
    from areal_rsi.agents.research.prompt import PROMPT as RESEARCH_PROMPT
    from areal_rsi.agents.rsi.prompt import PROMPT as RSI_PROMPT

    source = tmp_path / "prompt.md"
    source.write_text("CUSTOM ROLE")
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills/SKILL.md").write_text("CUSTOM SKILL")
    selected = str(source if component == "prompt" else tmp_path / "skills")
    config = config_from_dict(
        {
            "brief": {"objective": "Test", "instructions": "Check"},
            "agents": {role: {component: selected}},
        }
    )
    config.agents.freeze()  # Same path as programmatic/API run creation.
    agent = getattr(config.agents, role)
    assert agent.snapshot is not None
    default = {"research": RESEARCH_PROMPT, "rsi": RSI_PROMPT}[role]
    assert agent.snapshot.prompt == ("CUSTOM ROLE" if component == "prompt" else default)
    assert agent.snapshot.skills == ({} if component == "prompt" else {"SKILL.md": "CUSTOM SKILL"})
    other = "research" if role == "rsi" else "rsi"
    assert getattr(config.agents, other) is None
    write_json(tmp_path / "run/coordination/config.json", config.public())
    assert run_method(tmp_path / "run", role) == agent.snapshot
    assert run_method(tmp_path / "run", other) is None
