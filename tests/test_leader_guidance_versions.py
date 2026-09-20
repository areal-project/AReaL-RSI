"""Prompt ablations preserve the old recipe and freeze the selected version."""

from __future__ import annotations

import pytest

from areal_rsi.coordination.collaboration import (
    DEFAULT_MODULES,
    build_plan,
    manifest,
    presets,
    selected_modules,
)


def test_prompt_versions_preserve_old_recipes_and_share_the_runtime() -> None:
    old = build_plan("passk", ("leader_guidance",))
    v022 = build_plan("passk", ("leader_guidance", "leader_guidance_v022"))
    new = build_plan("passk", ("leader_guidance", "leader_guidance_v023"))
    # Recorded before moving Python strings to Markdown. Historical runs must
    # keep matching, including whitespace and formerly composed integrity text.
    fingerprints = {
        "passk": "8bca10efd4275ae6ea8daad76baf79d92653207ccedab34587ee30a287b8dda4",
        "passk_guided": "a4f46490154d44f4273a64c45e46deaf0703ba03578d325d9ab328ec1721d4f4",
        "passk_guided_v022": "3ded97c83ed0409563f75cb9029f754fc2171359cb81bb6d8ec44fc6a992b233",
        "passk_guided_v023": "36d605c42e4b46150cc2c2847d6d40c7f9a4a2acd0387d0dc321e56b1ad098a9",
        "passk_guided_v024": "00b77855d51d0aa267d302fd9ca4309a46c40bf79f329dff1108fb3aa8d37e1c",
    }
    for choice in presets():
        if choice["id"] in fingerprints:
            assert manifest(choice["mode"], tuple(choice["modules"]))["fingerprint"] == (
                fingerprints[choice["id"]]
            )
    assert v022.rsi_prompt != old.rsi_prompt
    assert v022.research_prompt == old.research_prompt
    assert new.rsi_prompt != v022.rsi_prompt
    assert new.research_prompt != v022.research_prompt
    assert new.actions is old.actions and new.engine is old.engine
    assert new.supervisor_only == old.supervisor_only
    continuous = build_plan("passk", DEFAULT_MODULES)
    assert continuous.actions is not new.actions and continuous.engine is not new.engine
    assert issubclass(continuous.actions, new.actions)
    assert issubclass(continuous.engine, new.engine)
    assert manifest("passk", DEFAULT_MODULES) == manifest("passk", DEFAULT_MODULES[::-1])
    with pytest.raises(ValueError, match="需要"):
        selected_modules("passk", ("leader_guidance_v022",))
    with pytest.raises(ValueError, match="冲突"):
        selected_modules("passk", (*DEFAULT_MODULES, "leader_guidance_v022"))
    with pytest.raises(ValueError, match="冲突"):
        selected_modules("passk", (*DEFAULT_MODULES, "leader_guidance_v023"))
