"""The advertised intake schema and executable contract change together with mode."""

import copy
from pathlib import Path
from typing import Any

import pytest

from areal_rsi.api.research import ResearchLimits, TaskBrief, VerificationContract
from areal_rsi.infra.coordination.tools.brief import PASSK_HIDDEN_LIMITS, brief_tool, updated_brief
from areal_rsi.infra.storage.files import encode


def arguments(mode: str) -> dict[str, Any]:
    value: dict[str, Any] = encode(
        TaskBrief("Improve benchmark", "Keep protocol", ResearchLimits())
    )
    if mode == "passk":
        for key in PASSK_HIDDEN_LIMITS:
            value["limits"].pop(key)
    return value


def test_structured_update_preserves_verification_identity() -> None:
    mode = "passk"
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
    assert result.verification is not None and current.verification is not None
    assert result.verification.identity == current.verification.identity
    assert result.limits.max_agents == 1
    for key in PASSK_HIDDEN_LIMITS:
        assert getattr(result.limits, key) is None


def test_effective_budget_is_per_researcher_and_only_available_in_passk() -> None:
    args = arguments("passk")
    args["limits"].update(max_effective_seconds=1800, max_wall_seconds=None)
    result = updated_brief("passk", TaskBrief("old", "old"), args)
    assert result.limits.max_effective_seconds == 1800
    assert result.limits.max_wall_seconds is None


@pytest.mark.parametrize("mode", ["passk"])
def test_removed_model_call_limit_is_not_advertised_or_accepted(mode: str) -> None:
    tool = brief_tool(mode)
    assert "max_model_calls" not in tool["parameters"]["properties"]["limits"]["properties"]
    args = arguments(mode)
    args["limits"]["max_model_calls"] = 200
    with pytest.raises(ValueError, match="Additional properties"):
        updated_brief(mode, TaskBrief("old", "old"), args)


def test_action_catalog_follows_current_mode(tmp_path: Path) -> None:
    from areal_rsi.infra.coordination.coordinator import Coordinator
    from areal_rsi.infra.coordination.tools.actions import ResearchActions

    coordinator = Coordinator.create(tmp_path, TaskBrief("test", "test"))
    actions = ResearchActions(coordinator)
    tools = actions.definitions("interaction")
    tool = next(tool for tool in tools if tool["name"] == "update_brief")
    assert tool == brief_tool("passk")
    coordinator.state.status = "running"
    assert all(tool["name"] != "update_brief" for tool in actions.definitions("interaction"))
