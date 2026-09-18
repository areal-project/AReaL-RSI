"""The advertised intake schema and executable contract change together with mode."""

import copy

import pytest
from jsonschema import Draft202012Validator

from areal_rsi.api.research import ResearchLimits, TaskBrief, VerificationContract
from areal_rsi.base.storage import encode
from areal_rsi.coordination.brief_tool import PASSK_HIDDEN_LIMITS, brief_tool, updated_brief


def arguments(mode: str) -> dict:
    value = encode(TaskBrief("Improve benchmark", "Keep protocol", ResearchLimits()))
    if mode == "passk":
        for key in PASSK_HIDDEN_LIMITS:
            value["limits"].pop(key)
    return value


def test_schema_changes_by_mode_without_mutating_other_catalogs() -> None:
    collaborative = brief_tool("collaborative")
    passk = brief_tool("passk")
    for tool in (collaborative, passk):
        Draft202012Validator.check_schema(tool["parameters"])
        assert "brief_json" not in tool["parameters"]["properties"]
    for key in PASSK_HIDDEN_LIMITS:
        assert key in collaborative["parameters"]["properties"]["limits"]["properties"]
        assert key not in passk["parameters"]["properties"]["limits"]["properties"]
    assert brief_tool("collaborative") == collaborative


@pytest.mark.parametrize("key", PASSK_HIDDEN_LIMITS)
@pytest.mark.parametrize("value", [None, 100])
def test_passk_rejects_hidden_keys_even_when_null(key: str, value: int | None) -> None:
    args = arguments("passk")
    args["limits"][key] = value
    with pytest.raises(ValueError, match="Additional properties"):
        updated_brief("passk", TaskBrief("old", "old"), args)


@pytest.mark.parametrize("mode", ["passk", "collaborative"])
def test_structured_update_preserves_verification_identity(mode: str) -> None:
    current = TaskBrief(
        "old",
        "old",
        verification=VerificationContract(
            "ppl", "minimize", identity={"dataset": "fixed", "seed": 1337}
        ),
    )
    args = arguments(mode)
    args["verification"] = {
        "metric": "ppl",
        "direction": "minimize",
        "target": 10.0,
        "description": "fixed dev",
    }
    original = copy.deepcopy(args)
    result = updated_brief(mode, current, args)
    assert args == original
    assert result.verification.identity == current.verification.identity
    assert result.limits.max_agents == 1
    for key in PASSK_HIDDEN_LIMITS:
        assert getattr(result.limits, key) == (None if mode == "passk" else args["limits"][key])


def test_rejects_legacy_string_and_bad_types() -> None:
    with pytest.raises(ValueError):
        updated_brief("passk", TaskBrief("old", "old"), {"brief_json": "{}"})
    args = arguments("collaborative")
    args["limits"]["max_jobs"] = "8"
    with pytest.raises(ValueError, match="max_jobs"):
        updated_brief("collaborative", TaskBrief("old", "old"), args)


def test_action_catalog_follows_current_mode(tmp_path) -> None:
    from areal_rsi.coordination.actions import ResearchActions
    from areal_rsi.coordination.coordinator import Coordinator

    coordinator = Coordinator.create(tmp_path, TaskBrief("test", "test"))
    actions = ResearchActions(coordinator)
    for mode in ("collaborative", "passk", "collaborative"):
        coordinator.state["mode"] = mode
        tools = actions.definitions("interaction")
        tool = next(tool for tool in tools if tool["name"] == "update_brief")
        assert tool == brief_tool(mode)
    coordinator.state["status"] = "running"
    assert all(tool["name"] != "update_brief" for tool in actions.definitions("interaction"))
