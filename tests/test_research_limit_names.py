from copy import deepcopy

import pytest

from areal_rsi.api.research import TaskBrief, normalize_brief_limits
from areal_rsi.base.storage import decode, encode
from areal_rsi.coordination.config import config_from_dict


def test_saved_limit_names_preserve_values_and_write_only_new_names() -> None:
    saved = {
        "brief": {
            "objective": "Existing research",
            "instructions": "",
            "limits": {"max_agents": 3, "max_nodes": None},
        }
    }
    original = deepcopy(saved)
    config = config_from_dict(saved)
    assert config.brief.limits.max_research_agents == 3
    assert config.brief.limits.max_research_tasks is None
    public = config.public()["brief"]["limits"]
    assert public["max_research_agents"] == 3 and public["max_research_tasks"] is None
    assert "max_agents" not in public and "max_nodes" not in public
    brief = decode(TaskBrief, normalize_brief_limits(saved["brief"]))
    assert encode(brief) == encode(config.brief)
    assert saved == original


def test_conflicting_limit_names_are_rejected_instead_of_overriding_a_budget() -> None:
    with pytest.raises(ValueError, match="only max_research_agents"):
        normalize_brief_limits({"limits": {"max_agents": 2, "max_research_agents": 5}})
    with pytest.raises(ValueError, match="only max_research_tasks"):
        normalize_brief_limits({"limits": {"max_nodes": None, "max_research_tasks": 4}})
