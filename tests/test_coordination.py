from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from areal_rsi.api.base import AgentState, IterationState
from areal_rsi.api.research import ResearchLimits, ResearchMessage, ResearchReport, TaskBrief
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


def test_retry_intent_survives_restart_and_is_bound_to_active_delivery(tmp_path: Path) -> None:
    c = create(tmp_path)
    message, _ = c.begin_turn("rsi")
    with pytest.raises(ValueError, match="failed"):
        c.retry_agent("rsi")
    c.end_turn("rsi", error="Per-turn call budget exhausted")
    c.retry_agent("rsi")
    reopened = Coordinator(c.store)
    retry = reopened.state["turn_retries"]["rsi"]
    assert retry["message_id"] == message.message_id
    assert retry["retry_id"]
    assert reopened.actor("rsi").state == AgentState.WAITING
    assert reopened.begin_turn("rsi")[1] is True
    reopened.end_turn("rsi")
    assert "rsi" not in Coordinator(c.store).state["turn_retries"]


def test_summary_reports_reservations_and_freezes_elapsed_after_stop(tmp_path: Path) -> None:
    c = Coordinator.create(tmp_path, TaskBrief("Inspect", "No initial version"))
    assert c.summary()["budget"]["elapsed_seconds"] == 0
    c.start()
    c.state["model_requests"] = {
        "settled": {"reserved_tokens": 0},
        "active": {"reserved_tokens": 18000},
        "cancelled_unknown_usage": {"reserved_tokens": 12000},
    }
    c.save()
    assert c.summary()["budget"]["reserved_tokens"] == 30000
    c.stop("End test")
    elapsed = c.summary()["budget"]["elapsed_seconds"]
    assert elapsed >= 0
    assert Coordinator(c.store).summary()["budget"]["elapsed_seconds"] == elapsed


def test_report_correction_retry_recovers_enqueue_gap_with_same_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = create(tmp_path)
    branch = c.spawn(key="a", direction="A", instruction="Inspect", completion_criteria="Report")
    agent_id = branch["agent_id"]
    original, _ = c.begin_turn(agent_id)
    c.end_turn(agent_id, error="Missing report", missing_report=True)

    def interrupted_enqueue(*args: Any, **kwargs: Any) -> str:
        raise RuntimeError("Process interrupted before corrective message")

    with monkeypatch.context() as patcher:
        patcher.setattr(c, "enqueue", interrupted_enqueue)
        with pytest.raises(RuntimeError, match="interrupted"):
            c.retry_agent(agent_id)
    recovered = Coordinator(c.store)
    retry_id = recovered.state["turn_retries"][agent_id]["retry_id"]
    assert original.message_id in recovered.state["delivered"]
    assert agent_id not in recovered.state["active"]
    recovered.retry_agent(agent_id)
    assert recovered.state["turn_retries"][agent_id]["retry_id"] == retry_id
    assert len(recovered.state["inboxes"][agent_id]) == 1
    corrective = recovered.store.get(
        "messages", recovered.state["inboxes"][agent_id][0], ResearchMessage
    )
    assert corrective.reply_to == original.message_id
    assert "不授权新任务" in corrective.text
    assert len(recovered.actor(agent_id).iterations) == 1
