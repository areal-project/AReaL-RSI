"""Prompt ablations preserve the old recipe and freeze the selected version."""

from __future__ import annotations

import pytest

from areal_rsi.coordination.collaboration import (
    DEFAULT_MODULES,
    build_plan,
    manifest,
    selected_modules,
)


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
    with pytest.raises(ValueError, match="需要"):
        selected_modules("passk", ("leader_guidance_v022",))
