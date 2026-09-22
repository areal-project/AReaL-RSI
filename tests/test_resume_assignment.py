from pathlib import Path

import pytest

from areal_rsi.api.research import ResearchLimits, TaskBrief
from areal_rsi.coordination.coordinator import Coordinator


def test_resume_preserves_node_report_and_budget(tmp_path: Path) -> None:
    c = Coordinator.create(tmp_path, TaskBrief("research", "", ResearchLimits(max_nodes=1)))
    c.start()
    agent = c.spawn(key="first", direction="A", instruction="complete quota")["agent_id"]
    node_id = c.actor(agent).current_iteration_id
    report = c.report(agent, key="premature", fields={"message": "waiting for job"})
    with pytest.raises(ValueError, match="budget exhausted"):
        c.assign(agent, key="second", instruction="continue")
    with pytest.raises(ValueError, match="current assignment"):
        c.resume_assignment(agent, report_id="wrong", instruction="continue", key="wrong")
    message_id = c.resume_assignment(
        agent, report_id=report["report_id"], instruction="finish original quota", key="resume"
    )
    assert c.resume_assignment(
        agent, report_id=report["report_id"], instruction="finish original quota", key="resume"
    ) == message_id
    actor = c.actor(agent)
    assert actor.current_iteration_id == node_id
    assert len(actor.iterations) == 1
    assert actor.iterations[0].report_ref is None
    assert any(report["report_id"] in ref.uri for ref in actor.iterations[0].references)
    assert c.store.get("reports", report["report_id"], dict)["message"] == "waiting for job"
    assert agent in c.pending()
