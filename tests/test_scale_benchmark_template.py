from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from areal_rsi.benchmarks.scale import catalog, prepare_config
from areal_rsi.research_app import ResearchApp


def test_scale_catalog_and_workbench_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = Path(__file__).parents[1]
    runtime = repository.parent / "scale_rsi_bench"
    if not (runtime / "swarm_env/source/tasks/agent-swarm-optimization").is_dir():
        pytest.skip("staged Scale RSI runtime is unavailable")
    monkeypatch.setenv("RSI_WORK_ROOT", str(runtime))
    assert {item["id"] for item in catalog(repository)} == {
        "nanogpt",
        "opsd",
        "jailbreak",
        "swarm",
    }
    config = prepare_config(
        tmp_path,
        repository,
        "research_swarm_ui",
        "swarm",
        max_agents=2,
        max_jobs=5,
        hours=3,
    )
    assert config.metadata["shared_agent_workspace"] is True
    assert config.brief.limits.max_agents == 2
    assert [command.name for command in config.commands] == ["experiment"]

    for benchmark in ("nanogpt", "opsd", "jailbreak", "swarm"):
        run_id = f"research_{benchmark}_instructions"
        prepare_config(tmp_path, repository, run_id, benchmark, max_jobs=1)
        workspace = tmp_path / "runs" / run_id / "setup/benchmark-session/workspace"
        instructions = (workspace / "instruction.md").read_text()
        assert 'run_job(command_name="experiment"' in instructions
        assert "cannot call `sbatch`" in instructions
        if benchmark == "opsd":
            assert (workspace / "eval_official.py").is_file()

    unlimited_ceiling = prepare_config(
        tmp_path,
        repository,
        "research_swarm_many_jobs",
        "swarm",
        max_jobs=1000,
    )
    assert unlimited_ceiling.brief.limits.max_jobs == 1000


def test_research_app_creates_benchmark_from_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = Path(__file__).parents[1]
    runtime = repository.parent / "scale_rsi_bench"
    if not (runtime / "swarm_env/source/tasks/agent-swarm-optimization").is_dir():
        pytest.skip("staged Scale RSI runtime is unavailable")
    monkeypatch.setenv("RSI_WORK_ROOT", str(runtime))
    monkeypatch.setenv("RSI_API_KEY", "fixture")

    async def scenario() -> None:
        app = ResearchApp(tmp_path, repository=repository)
        bootstrap = await app.call("bootstrap", {})
        assert {item["id"] for item in bootstrap["benchmark_templates"]} >= {"swarm"}
        detail = await app.call(
            "create",
            {
                "run_id": "research_swarm_ui",
                "benchmark": "swarm",
                "hours": 3,
                "max_jobs": 5,
                "max_agents": 1,
                "model_settings": {
                    "base_url": "https://example.com/v1",
                    "model": "gpt-5.6-luna",
                    "reasoning_effort": "medium",
                    "backend": "standard",
                    "api_key": "fixture",
                },
            },
        )
        assert detail["status"] == "planned"
        assert (tmp_path / "runs/research_swarm_ui/setup/benchmark-session/session.json").is_file()
        assert (tmp_path / "runs/research_swarm_ui/candidates").is_dir()

    asyncio.run(scenario())
