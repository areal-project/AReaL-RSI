"""Collaboration recipes bind real behavior, survive restart, and freeze at start."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest
from runtime_fakes import ActionRuntime

from areal_rsi.api.research import TaskBrief
from areal_rsi.infra.coordination.config import ResearchConfig, config_from_dict
from areal_rsi.infra.coordination.intake import ResearchIntake
from areal_rsi.infra.coordination.policies.collaboration import (
    MODULES,
    build_plan,
    selected_modules,
)
from areal_rsi.infra.coordination.policies.passk import PassKActions, PassKEngine
from areal_rsi.infra.research import ResearchSystem
from areal_rsi.infra.storage.files import write_json


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
    assert plan.actions is PassKActions
    assert plan.supervisor_only
    assert restored.brief.limits.max_wall_seconds is None
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
            assert isinstance(active.engine, PassKEngine)
            assert active.engine.rsi_prompt == plan.rsi_prompt
            assert active.engine.research_prompt == plan.research_prompt
            assert "你的默认工作区" in active.engine.tools_factory("rsi").instructions
            # Record a complete intake before testing frozen collaboration identity.
            brief = c.store.brief()
            brief.objective = brief.instructions = "Review the task"
            brief.acceptance = ["Provide an evidence-backed review"]
            run = c.store.run()
            run.brief_ref = c.store.put("briefs", "ready", brief)
            c.store.save_run(run)
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
