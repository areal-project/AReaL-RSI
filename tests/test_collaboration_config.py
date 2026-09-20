"""Collaboration recipes bind real behavior, survive restart, and freeze at start."""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from areal_rsi.api.research import TaskBrief
from areal_rsi.base.storage import write_json
from areal_rsi.coordination.collaboration import (
    DEFAULT_MODULES,
    MODULES,
    build_plan,
    selected_modules,
)
from areal_rsi.coordination.config import ResearchConfig, config_from_dict
from areal_rsi.coordination.intake import ResearchIntake
from areal_rsi.coordination.passk import PassKActions, PassKEngine
from areal_rsi.modules.responses import ResponsesConfig, ResponsesRuntime
from areal_rsi.research_app import ResearchApp
from areal_rsi.ui.launch import WorkbenchClient
from areal_rsi.ui.workbench import WorkbenchServer


class IdleTransport:
    async def request(self, config: Any, payload: Any, sink: Any) -> dict[str, Any]:
        await asyncio.Event().wait()
        raise AssertionError("cancelled before a model response")


def app_at(root: Path) -> ResearchApp:
    return ResearchApp(
        root,
        runtime_factory=lambda _: ResponsesRuntime(
            ResponsesConfig(api_key="test", base_url="https://example.test"), IdleTransport()
        ),
    )


def test_default_config_and_unimplemented_modules() -> None:
    config = ResearchConfig(TaskBrief("task", "complete task"))
    legacy = config.public()
    legacy.pop("collaboration_modules")
    assert config_from_dict(legacy).mode == "passk"
    with pytest.raises(ValueError, match="已移除"):
        config_from_dict({**legacy, "mode": "collaborative"})
    ResearchApp.select_collaboration(config, {"mode": "passk", "modules": []})
    restored = config_from_dict(config.public())
    plan = build_plan(restored.mode, restored.collaboration_modules)
    assert plan.actions is PassKActions and plan.engine is PassKEngine
    assert plan.supervisor_only
    assert restored.brief.limits.max_wall_seconds == 14400
    assert restored.brief.limits.max_model_calls is None
    assert restored.brief.limits.max_total_tokens is None
    assert restored.brief.limits.max_verify_calls is None
    restored.brief.limits.max_effective_seconds = 60
    with pytest.raises(ValueError, match="standard"):
        restored.validate_clock()
    restored.backend = "codex"
    restored.validate_clock()
    with pytest.raises(ValueError, match="尚未实现"):
        config_from_dict({**config.public(), "collaboration_modules": ["evidence_pool"]})
    with pytest.raises(ValueError, match="名称列表"):
        selected_modules("passk", "invalid")  # type: ignore[arg-type]


def test_selection_rebuilds_runtime_and_freezes_versions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = MODULES["leader_guidance"]
    plan = build_plan("passk", (module.id,))

    async def scenario() -> None:
        app = app_at(tmp_path)
        try:
            detail = await app.create(
                {"conversation": True, "collaboration": {"mode": "passk", "modules": []}}
            )
            c = app.coordinator(detail["run_id"])
            original = await app.attach(c)
            await app.set_collaboration(c, {"mode": "passk", "modules": [module.id]})
            active = app.active[c.store.run_id]
            assert active is not original
            assert isinstance(active.actions, plan.actions)
            assert isinstance(active.engine, plan.engine)
            assert active.engine.rsi_prompt == plan.rsi_prompt
            assert active.engine.research_prompt == plan.research_prompt
            assert not active.tools.supervisor_only
            assert c.store.brief().limits.max_model_calls is None
            # Starting directly here avoids invoking a real Interaction agent.
            c.state["intake"]["status"] = "ready"
            c.save()
            await app.start(c)
            path = c.store.root / "coordination/collaboration.json"
            frozen = path.read_bytes()
            detail = await app.detail(c.store.run_id)
            assert not detail["collaboration"]["editable"]
            assert detail["collaboration"]["manifest"]["modules"] == [
                {"id": module.id, "version": module.version}
            ]
            for state in ("running", "paused", "stopped"):
                c.state["status"] = state
                c.save()
                with pytest.raises(ValueError, match="已锁定"):
                    await app.set_collaboration(c, {"mode": "passk", "modules": []})
            assert path.read_bytes() == frozen
            await app.close()
            restored = app_at(tmp_path)
            try:
                recovered = await restored.attach(restored.coordinator(c.store.run_id))
                assert isinstance(recovered.actions, plan.actions)
                await restored.close()
                monkeypatch.setitem(MODULES, module.id, replace(module, version="changed"))
                with pytest.raises(ValueError, match="冻结"):
                    await restored.attach(restored.coordinator(c.store.run_id))
                assert path.read_bytes() == frozen
            finally:
                await restored.close()
        finally:
            await app.close()

    asyncio.run(scenario())


def test_environment_import_preserves_selected_collaboration(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = app_at(tmp_path)
        try:
            detail = await app.create(
                {
                    "conversation": True,
                    "collaboration": {"mode": "passk", "modules": []},
                }
            )
            c = app.coordinator(detail["run_id"])
            active = await app.attach(c)
            intake = ResearchIntake(
                active.actions, repository=app.repository, start=lambda message: app.start(c)
            )
            incoming = ResearchConfig(TaskBrief("environment", "adapter instructions"))
            intake.import_config(incoming, "loaded adapter")
            assert app.config(c).mode == "passk"
            assert app.config(c).collaboration_modules == ()
        finally:
            await app.close()

    asyncio.run(scenario())


def test_http_selection_and_historical_read_only_projection(tmp_path: Path) -> None:
    app = app_at(tmp_path)
    server = WorkbenchServer(app, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = WorkbenchClient(server.server_port)
        bootstrap = client.request("/api/bootstrap")
        client.token = bootstrap["csrf_token"]
        assert bootstrap["default_collaboration"]["modules"] == list(DEFAULT_MODULES)
        assert [m["id"] for m in bootstrap["collaboration_catalog"]["modes"]] == ["passk"]
        assert "leader_guidance" in {m["id"] for m in bootstrap["collaboration_catalog"]["modules"]}
        assert bootstrap["collaboration_catalog"]["presets"][1]["modules"] == ["leader_guidance"]
        assert bootstrap["collaboration_catalog"]["presets"][2]["modules"] == [
            "leader_guidance",
            "leader_guidance_v022",
        ]
        default_preset = next(
            preset
            for preset in bootstrap["collaboration_catalog"]["presets"]
            if preset["id"] == "passk_guided_v024"
        )
        assert default_preset["modules"] == list(DEFAULT_MODULES)
        detail = client.request(
            "/api/runs",
            {
                "message": "",
                "collaboration": {"mode": "passk", "modules": []},
            },
        )
        run_id = detail["run_id"]
        detail = client.request(f"/api/runs/{run_id}")
        assert detail["collaboration"]["mode"] == "passk"
        assert detail["collaboration"]["editable"]
        assert detail["brief"]["limits"]["max_verify_calls"] is None
        client.request(
            f"/api/runs/{run_id}/collaboration",
            {
                "mode": "passk",
                "modules": ["leader_guidance"],
            },
        )
        updated = client.request(f"/api/runs/{run_id}")
        assert updated["collaboration"]["mode"] == "passk"
        assert updated["collaboration"]["modules"] == ["leader_guidance"]
        # A pre-feature stopped run has no manifest. Reading it never rewrites
        # frozen config to pretend its implementation version was recorded.
        c = app.coordinator(run_id)
        c.state.update(status="stopped", started_at="2026-09-16T10:00:00Z")
        c.save()
        path = c.store.root / "coordination/config.json"
        config = json.loads(path.read_text())
        config.pop("collaboration_modules")
        config.pop("mode")  # Pre-mode historical configuration.
        config["research_prompt_variant"] = "detailed"
        write_json(path, config)
        before = path.read_bytes()
        archived = client.request(f"/api/runs/{run_id}")
        assert archived["collaboration"]["mode"] == "collaborative"
        assert not archived["collaboration"]["supported"]
        assert not archived["collaboration"]["editable"]
        assert not archived["model_settings"]["editable"]
        for action in ("start", "resume", "chat"):
            with pytest.raises(ValueError, match="已移除"):
                client.request(f"/api/runs/{run_id}/{action}", {"text": "continue"})

        assert archived["collaboration"]["manifest"] is None
        assert path.read_bytes() == before
        assert not (c.store.root / "coordination/collaboration.json").exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_recovery_skips_retired_mode_without_writing_history(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = app_at(tmp_path)
        try:
            detail = await app.create({"conversation": True})
            c = app.coordinator(detail["run_id"])
            c.state.update(status="running", started_at="2026-09-16T10:00:00Z")
            c.save()
            path = c.store.root / "coordination/config.json"
            config = json.loads(path.read_text())
            config.update(
                mode="collaborative", collaboration_modules=[], research_prompt_variant="simple"
            )
            config["metadata"]["auto_resume"] = True
            write_json(path, config)
            before = {p: p.read_bytes() for p in c.store.root.rglob("*") if p.is_file()}
            await app.call("recover", {})
            assert not app.active
            assert not (await app.detail(c.store.run_id))["collaboration"]["supported"]
            assert before == {p: p.read_bytes() for p in c.store.root.rglob("*") if p.is_file()}
            # Historical workers cannot resume, but operators can still clean them up.
            await app.call("stop", {"run_id": c.store.run_id})
            assert app.coordinator(c.store.run_id).state["status"] == "stopped"
            assert json.loads(path.read_text()) == config
            await app.call("delete", {"run_id": c.store.run_id})
            assert not c.store.root.exists()
        finally:
            await app.close()

    asyncio.run(scenario())
