"""Web development preparation is durable and must precede model execution."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

from areal_rsi.api.interfaces import ResearchAgentRuntime
from areal_rsi.infra.coordination.config import ResearchConfig
from areal_rsi.infra.research import ResearchSystem


@pytest.mark.parametrize("wall_seconds", ["omitted", None, 14400])
def test_preparation_survives_restart_and_invalidates_changed_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wall_seconds: str | int | None,
) -> None:
    monkeypatch.setenv("RSI_API_KEY", "fixture")
    monkeypatch.delenv("RSI_BACKEND", raising=False)
    monkeypatch.delenv("RSI_RUNTIME_CONFIG", raising=False)
    script = tmp_path / "prepare.py"
    script.write_text(
        "import json,sys,pathlib\n"
        "request=json.loads(pathlib.Path(sys.argv[1]).read_text())\n"
        "output=pathlib.Path(sys.argv[2])\n"
        "(output/'config.json').write_text(json.dumps(request['config']))\n"
        "(output/'checks.json').write_text(json.dumps([{'name':'Workspace',"
        "'status':'passed','message':'Ready'}]))\n"
    )
    setup = {"profile_id": "dev", "repository": "/project", "revision": "main",
             "acceptance": "Tests pass", "parallelism": 2}
    if wall_seconds != "omitted":
        setup["wall_seconds"] = wall_seconds
    profiles = tmp_path / "development-profiles"
    profiles.mkdir()
    (profiles / "dev.json").write_text(json.dumps({
        "profile_id": "dev", "title": "Development", "description": "Isolated workspace",
        "defaults": setup,
        "prepare_command": [sys.executable, str(script), "{request}", "{output}"],
    }))

    async def scenario() -> None:
        def no_models(_: ResearchConfig) -> ResearchAgentRuntime:
            raise AssertionError("Preparation must not start agents")

        app = ResearchSystem(tmp_path, runtime_factory=no_models)
        await app.create({"run_id": "dev", "conversation": True, "message": "Implement feature",
                          "development": setup, "collaboration": {"mode": "passk", "modules": []}})
        with pytest.raises(ValueError, match="预检"):
            await app.start("dev")
        with pytest.raises(ValueError, match="预检"):
            await app.send_message("dev", "Do not start yet")
        with pytest.raises(ValueError, match="预检"):
            await app.resume("dev")
        with pytest.raises(ValueError, match="预检"):
            await app.retry_agent("dev", "rsi")
        await app.prepare("dev")
        preparation = app.coordinator("dev").state.preparation
        assert preparation is not None
        identifier = preparation.job_id
        await app.prepare("dev")
        assert preparation.job_id == identifier
        await app.close()
        app = ResearchSystem(tmp_path, runtime_factory=no_models)
        try:
            owner = app.coordinator("dev")
            assert owner.state.preparation is not None
            before = owner.state.preparation.status
            owner.acquire()
            try:
                assert (await app.detail("dev"))["preparation"]["status"] == before
            finally:
                owner.release()
            async with asyncio.timeout(10):
                while (await app.detail("dev"))["preparation"]["status"] == "running":
                    await asyncio.sleep(.05)
            detail = (await app.detail("dev"))
            assert detail["preparation"]["status"] == "ready", detail["preparation"]
            ready_job = detail["preparation"]["job_id"]
            await app.prepare("dev", wait=True)
            reused = app.coordinator("dev").state.preparation
            assert reused is not None and reused.job_id == ready_job
            assert detail["preparation"]["checks"][0]["status"] == "passed"
            assert detail["budget"]["total_tokens"] == 0
            expected_limit = None if wall_seconds == "omitted" else wall_seconds
            limits = app.config(app.coordinator("dev")).brief.limits
            assert limits.max_wall_seconds == expected_limit
            assert detail["preparation"]["setup"]["wall_seconds"] == expected_limit
            with pytest.raises(ValueError, match="开始研究"):
                await app.resume("dev")
            settings = app.config(app.coordinator("dev")).model_settings()
            await app.set_model_settings("dev", {**settings, "model": "different"})
            assert (await app.detail("dev"))["preparation"]["status"] == "pending"
            with pytest.raises(ValueError, match="预检"):
                await app.start("dev")
            script.write_text("import time\ntime.sleep(30)\n")
            await app.prepare("dev")
            await app.cancel("dev")
            async with asyncio.timeout(10):
                while (await app.detail("dev"))["preparation"]["status"] == "cancelling":
                    await asyncio.sleep(.05)
            cancelled = (await app.detail("dev"))["preparation"]
            assert cancelled["status"] == "failed"
            assert "取消" in cancelled["error"]
        finally:
            await app.close()

    asyncio.run(scenario())


def test_workspace_creation_uses_server_context_and_user_requirements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime_fakes import ActionRuntime

    from areal_rsi.api.research import TaskBrief

    monkeypatch.setenv("RSI_API_KEY", "fixture")
    profiles = tmp_path / "development-profiles"
    profiles.mkdir()
    defaults = {"profile_id": "project", "repository": "/operator/repo", "revision": "fixed",
                "acceptance": "Old unrelated example", "parallelism": 3, "wall_seconds": 14400}
    registered = {"profile_id": "project", "title": "Internal JSON example", "description": "",
                  "defaults": defaults, "prepare_command": ["true"]}
    (profiles / "project.json").write_text(json.dumps(registered))

    async def scenario() -> None:
        app = ResearchSystem(tmp_path, runtime_factory=lambda _: ActionRuntime())
        request = "Add login verification; preserve existing login; include regression tests."
        try:
            assert (await app.bootstrap())["workspace_ready"]
            await app.create({"run_id": "natural", "message": request, "use_workspace": True})
            c = app.coordinator("natural")
            assert c.state.preparation is not None
            assert c.state.preparation.setup.repository == "/operator/repo"
            assert c.state.preparation.setup.revision == "fixed"
            assert c.state.preparation.setup.parallelism == 1
            assert c.state.preparation.setup.wall_seconds is None
            assert c.state.preparation.setup.acceptance == request
            assert c.store.brief().instructions == request
            assert c.state.status == "planned" and not c.store.run().research_agents
            assert c.state.intake.status == "discussing"
            assert c.state.preparation.job_id is None
            await app.create({"run_id": "single", "message": request, "use_workspace": True,
                              "wall_seconds": 3600,
                              "collaboration": {"mode": "passk", "modules": [], "max_agents": 1}})
            brief = app.coordinator("single").store.brief()
            assert brief.limits.max_agents == 1 and brief.limits.max_wall_seconds == 3600
            registered["profile_id"] = "other"
            (profiles / "other.json").write_text(json.dumps(registered))
            assert not (await app.bootstrap())["workspace_ready"]
            with pytest.raises(ValueError, match="工作区尚未配置"):
                await app.create({"message": request, "use_workspace": True})
        finally:
            await app.close()
        configured = ResearchSystem(tmp_path, config=ResearchConfig(
            TaskBrief("Project", ""), metadata={"development_profile_id": "project"}))
        try:
            assert (await configured.bootstrap())["workspace_ready"]
        finally:
            await configured.close()

    asyncio.run(scenario())
