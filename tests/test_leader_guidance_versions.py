"""Prompt ablations preserve the old recipe and freeze the selected version."""

from __future__ import annotations

import pytest

from areal_rsi.infra.coordination.policies.collaboration import (
    DEFAULT_MODULES,
    build_plan,
    manifest,
    selected_modules,
)


def test_v022_changes_only_leader_prompt_and_preserves_v021() -> None:
    old = build_plan("passk", ("leader_guidance",))
    modules = ("leader_guidance", "leader_guidance_v022")
    new = build_plan("passk", modules)
    assert DEFAULT_MODULES == ()
    assert manifest("passk", modules)["mode_version"] == "3"
    assert new.rsi_prompt != old.rsi_prompt
    assert new.research_prompt == old.research_prompt
    assert new.actions is old.actions
    assert new.supervisor_only == old.supervisor_only
    assert manifest("passk", modules) == manifest("passk", modules[::-1])
    with pytest.raises(ValueError, match="需要"):
        selected_modules("passk", ("leader_guidance_v022",))
