"""Collaboration recipes bind real behavior, survive restart, and freeze at start."""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import replace
from pathlib import Path

import pytest
from runtime_fakes import ActionRuntime

from areal_rsi.api.research import TaskBrief
from areal_rsi.infra.coordination.config import ResearchConfig, config_from_dict
from areal_rsi.infra.coordination.intake import ResearchIntake
from areal_rsi.infra.coordination.policies.collaboration import (
    DEFAULT_MODULES,
    MODULES,
    build_plan,
    selected_modules,
)
from areal_rsi.infra.coordination.policies.passk import PassKActions, PassKEngine
from areal_rsi.infra.research import ResearchSystem
from areal_rsi.infra.storage.files import write_json
from areal_rsi.ui.launch import WorkbenchClient
from areal_rsi.ui.workbench import WorkbenchServer


def system_at(root: Path) -> ResearchSystem:
    return ResearchSystem(root, runtime_factory=lambda _: ActionRuntime())


def test_default_config_and_unimplemented_modules() -> None:
    config = ResearchConfig(TaskBrief("task", "complete task"))
    legacy = config.public()
    legacy.pop("collaboration_modules")
    assert config_from_dict(legacy).mode == "passk"
    with pytest.raises(ValueError, match="已移除"):
        config_from_dict({**legacy, "mode": "collaborative"})
    ResearchSystem.select_collaboration(config, {"mode": "passk", "modules": []})
    restored = config_from_dict(config.public())
    plan = build_plan(restored.mode, restored.collaboration_modules)
    assert plan.actions is PassKActions and plan.engine is PassKEngine
    assert plan.supervisor_only
    assert restored.brief.limits.max_wall_seconds == 14400
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
        system = system_at(tmp_path)
        try:
            detail = await system.create(
                {"conversation": True, "collaboration": {"mode": "passk", "modules": []}}
            )
            c = system.coordinator(detail["run_id"])
            original = await system.attach(c)
            await system.set_collaboration(
                c.store.run_id, {"mode": "passk", "modules": [module.id]}
            )
            active = system.active[c.store.run_id]
            assert active is not original
            assert isinstance(active.actions, plan.actions)
            assert isinstance(active.engine, plan.engine)
            assert active.engine.rsi_prompt == plan.rsi_prompt
            assert active.engine.research_prompt == plan.research_prompt
            assert "你的默认工作区" in active.engine.tools_factory("rsi").instructions
            # Starting directly here avoids invoking a real Interaction agent.
            c.state.intake.status = "ready"
            c.save()
            await system.start(c.store.run_id)
            path = c.store.root / "coordination/collaboration.json"
            frozen = path.read_bytes()
            detail = await system.detail(c.store.run_id)
            assert not detail["collaboration"]["editable"]
            assert detail["collaboration"]["manifest"]["modules"] == [
                {"id": module.id, "version": module.version}
            ]
            for state in ("running", "paused", "stopped"):
                c.state.status = state
                c.save()
                with pytest.raises(ValueError, match="已锁定"):
                    await system.set_collaboration(c.store.run_id, {"mode": "passk", "modules": []})
            assert path.read_bytes() == frozen
            await system.close()
            restored = system_at(tmp_path)
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
            await system.close()

    asyncio.run(scenario())


def test_environment_import_preserves_selected_collaboration(tmp_path: Path) -> None:
    async def scenario() -> None:
        system = system_at(tmp_path)
        try:
            detail = await system.create(
                {
                    "conversation": True,
                    "collaboration": {"mode": "passk", "modules": []},
                }
            )
            c = system.coordinator(detail["run_id"])
            active = await system.attach(c)
            intake = ResearchIntake(
                active.actions,
                repository=system.repository,
                start=lambda message: system.start(c.store.run_id),
            )
            incoming = ResearchConfig(TaskBrief("environment", "adapter instructions"))
            intake.import_config(incoming, "loaded adapter")
            assert system.config(c).mode == "passk"
            assert system.config(c).collaboration_modules == ()
        finally:
            await system.close()

    asyncio.run(scenario())


def test_http_selection_and_historical_read_only_projection(tmp_path: Path) -> None:
    system = system_at(tmp_path)
    server = WorkbenchServer(system, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = WorkbenchClient(server.server_port)
        bootstrap = client.request("/api/bootstrap")
        client.token = bootstrap["csrf_token"]
        assert bootstrap["default_collaboration"]["modules"] == list(DEFAULT_MODULES)
        assert {m["id"] for m in bootstrap["collaboration_catalog"]["modes"]} == {
            "passk", "pass1"
        }
        assert "leader_guidance" in {m["id"] for m in bootstrap["collaboration_catalog"]["modules"]}
        assert bootstrap["collaboration_catalog"]["presets"][1]["modules"] == ["leader_guidance"]
        assert bootstrap["collaboration_catalog"]["presets"][2]["modules"] == list(DEFAULT_MODULES)
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
        c = system.coordinator(run_id)
        c.state.status, c.state.started_at = "stopped", "2026-09-16T10:00:00Z"
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
                client.request(
                    f"/api/runs/{run_id}/{action}", {"text": "continue"} if action == "chat" else {}
                )

        assert archived["collaboration"]["manifest"] is None
        assert path.read_bytes() == before
        assert not (c.store.root / "coordination/collaboration.json").exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_recovery_skips_retired_mode_without_writing_history(tmp_path: Path) -> None:
    async def scenario() -> None:
        system = system_at(tmp_path)
        try:
            detail = await system.create({"conversation": True})
            c = system.coordinator(detail["run_id"])
            c.state.status, c.state.started_at = "running", "2026-09-16T10:00:00Z"
            c.save()
            path = c.store.root / "coordination/config.json"
            config = json.loads(path.read_text())
            config.update(
                mode="collaborative", collaboration_modules=[], research_prompt_variant="simple"
            )
            config["metadata"]["auto_resume"] = True
            write_json(path, config)
            before = {p: p.read_bytes() for p in c.store.root.rglob("*") if p.is_file()}
            await system.call("recover", {})
            assert not system.active
            assert not (await system.detail(c.store.run_id))["collaboration"]["supported"]
            assert before == {p: p.read_bytes() for p in c.store.root.rglob("*") if p.is_file()}
            # Historical workers cannot resume, but operators can still clean them up.
            await system.call("stop", {"run_id": c.store.run_id})
            assert system.coordinator(c.store.run_id).state.status == "stopped"
            assert json.loads(path.read_text()) == config
            await system.call("delete", {"run_id": c.store.run_id})
            assert not c.store.root.exists()
        finally:
            await system.close()

    asyncio.run(scenario())
