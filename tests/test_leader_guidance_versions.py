"""Prompt ablations preserve the old recipe and freeze the selected version."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from test_collaboration_config import app_at

from areal_rsi.coordination.collaboration import DEFAULT_MODULES, build_plan, manifest


def test_v022_changes_only_leader_prompt_and_preserves_v021() -> None:
    old = build_plan("passk", ("leader_guidance",))
    new = build_plan("passk", DEFAULT_MODULES)
    # Recorded v0.2.1 identity: changing this would invalidate historical runs.
    assert manifest("passk", ("leader_guidance",))["fingerprint"] == (
        "a4f46490154d44f4273a64c45e46deaf0703ba03578d325d9ab328ec1721d4f4"
    )
    assert new.rsi_prompt != old.rsi_prompt
    assert new.research_prompt == old.research_prompt
    assert new.actions is old.actions and new.engine is old.engine
    assert new.supervisor_only == old.supervisor_only
    assert manifest("passk", DEFAULT_MODULES) == manifest("passk", DEFAULT_MODULES[::-1])


def test_new_default_and_switching_versions_survive_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = app_at(tmp_path)
        try:
            bootstrap = await app.call("bootstrap", {})
            assert bootstrap["default_collaboration"]["modules"] == list(DEFAULT_MODULES)
            choices = bootstrap["collaboration_catalog"]["presets"]
            assert [p["modules"] for p in choices] == [
                [],
                ["leader_guidance"],
                list(DEFAULT_MODULES),
            ]
            detail = await app.create({"conversation": True})
            c = app.coordinator(detail["run_id"])
            assert app.config(c).collaboration_modules == DEFAULT_MODULES
            for modules in ((), ("leader_guidance",), DEFAULT_MODULES):
                await app.set_collaboration(c, {"mode": "passk", "modules": list(modules)})
                active = await app.attach(c)
                assert active.engine.rsi_prompt == build_plan("passk", modules).rsi_prompt
            c.state["intake"]["status"] = "ready"
            c.save()
            await app.start(c)
            path = c.store.root / "coordination/collaboration.json"
            frozen = path.read_bytes()
            assert json.loads(frozen) == manifest("passk", DEFAULT_MODULES)
            with pytest.raises(ValueError, match="已锁定"):
                await app.set_collaboration(c, {"mode": "passk", "modules": ["leader_guidance"]})
            await app.close()
            app = app_at(tmp_path)
            active = await app.attach(app.coordinator(detail["run_id"]))
            assert active.engine.rsi_prompt == build_plan("passk", DEFAULT_MODULES).rsi_prompt
            assert path.read_bytes() == frozen
        finally:
            await app.close()

    asyncio.run(scenario())
