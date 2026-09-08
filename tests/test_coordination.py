from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from areal_rsi.api.base import AgentState, IterationState
from areal_rsi.api.research import ResearchLimits, ResearchReport, TaskBrief
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.coordination.store import ResearchStore


def create(root: Path, *, max_agents: int = 2) -> Coordinator:
    coordinator = Coordinator.create(
        root,
        TaskBrief(
            "Research useful directions",
            "No mandatory baseline or product",
            ResearchLimits(max_agents=max_agents),
        ),
    )
    coordinator.start()
    return coordinator


def report_fields() -> dict[str, str]:
    return {
        "task_status": "Completed the requested environment check",
        "work": "Read configuration and compared supported GPU options",
        "problems": "No GPU is provisioned yet",
        "results": "The configured runner accepts a GPU allocation",
        "interpretation": "Interface compatibility is confirmed; runtime performance is untested",
        "next_steps": "Run a synthetic job to verify cancellation and output capture",
    }


def test_report_is_a_hard_barrier_and_session_is_continuous(tmp_path: Path) -> None:
    c = create(tmp_path)
    first = c.spawn(
        key="first",
        direction="Execution reliability",
        instruction="Inspect runner",
        completion_criteria="Document actual supported invocation",
    )
    agent_id = first["agent_id"]
    assert agent_id in c.pending()
    message, resumed = c.begin_turn(agent_id)
    assert not resumed and message.sender == "rsi"
    session_id = c.actor(agent_id).session_id
    with pytest.raises(ValueError, match="finish"):
        c.assign(
            agent_id,
            key="premature",
            instruction="Run an experiment",
            completion_criteria="Save result",
        )
    submitted = c.report(agent_id, key="report", fields=report_fields())
    c.end_turn(agent_id)
    assert c.actor(agent_id).state == AgentState.WAITING
    assert c.actor(agent_id).wait_reason == "rsi_reply"
    assert agent_id not in c.pending()
    with pytest.raises(ValueError, match="latest report"):
        c.assign(
            agent_id,
            key="bad-reply",
            instruction="Run a synthetic job",
            completion_criteria="Observe one completion",
            reply_to_report_id="wrong",
        )
    second = c.assign(
        agent_id,
        key="next",
        instruction="Run a synthetic job",
        completion_criteria="Observe one completion",
        reply_to_report_id=submitted["report_id"],
    )
    assert c.actor(agent_id).session_id == session_id
    assert c.actor(agent_id).iterations[-1].parent_iteration_id == first["iteration_id"]
    assert second["iteration_id"] != first["iteration_id"]
    assert agent_id in c.pending()


def test_recovery_and_idempotent_messages_and_assignments(tmp_path: Path) -> None:
    c = create(tmp_path)
    arguments: dict[str, Any] = {
        "key": "assignment",
        "direction": "Paper search",
        "instruction": "Read paper",
        "completion_criteria": "Summarize evidence and limitations",
    }
    first = c.spawn(**arguments)
    message, resumed = c.begin_turn(first["agent_id"])
    assert not resumed
    reopened = Coordinator(ResearchStore(tmp_path, c.store.run_id))
    replay = reopened.spawn(**arguments)
    assert replay == first
    again, resumed = reopened.begin_turn(first["agent_id"])
    assert resumed and message.message_id == again.message_id
    assert len(reopened.actor(first["agent_id"]).iterations) == 1
    result = reopened.report(first["agent_id"], key="report", fields=report_fields())
    assert reopened.report(first["agent_id"], key="report", fields=report_fields()) == result
    assert len(reopened.store.records("reports", ResearchReport)) == 1
    assert len(reopened.state["inboxes"]["rsi"]) == 2  # Initial task plus one report.


def test_stop_archives_late_reports_without_restarting(tmp_path: Path) -> None:
    c = create(tmp_path)
    first = c.spawn(key="a", direction="A", instruction="Inspect A", completion_criteria="Report A")
    agent_id = first["agent_id"]
    c.begin_turn(agent_id)
    c.stop_agent(agent_id, "Direction is no longer useful")
    result = c.report(agent_id, key="late", fields=report_fields())
    c.end_turn(agent_id)
    assert result["status"] == "archived_after_stop"
    assert c.actor(agent_id).state == AgentState.STOPPED
    assert c.actor(agent_id).iterations[-1].state == IterationState.CANCELLED
    assert agent_id not in c.pending()
    assert len(c.state["inboxes"]["rsi"]) == 1


def test_parallel_branches_and_rsi_can_continue_work(tmp_path: Path) -> None:
    c = create(tmp_path)
    first = c.spawn(
        key="a", direction="A", instruction="Investigate A", completion_criteria="Report"
    )
    second = c.spawn(
        key="b", direction="B", instruction="Investigate B", completion_criteria="Report"
    )
    assert set(c.pending()) == {"rsi", first["agent_id"], second["agent_id"]}
    with pytest.raises(ValueError, match="limit"):
        c.spawn(key="c", direction="C", instruction="Investigate C", completion_criteria="Report")
    c.pause()
    assert not c.pending()
    c.resume()
    assert len(c.pending()) == 3
    c.stop("User stopped the run")
    assert not c.pending()
    with pytest.raises(ValueError, match="paused"):
        c.resume()
