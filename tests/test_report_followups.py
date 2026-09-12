"""An acknowledged report must not silently disappear from idle scheduling."""

from pathlib import Path

import pytest

from areal_rsi.api.base import AgentState
from areal_rsi.api.research import ResearchMessage, TaskBrief
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.coordination.store import ResearchStore
from areal_rsi.coordination.views import model_state


def reported(tmp_path: Path) -> tuple[Coordinator, str]:
    c = Coordinator.create(tmp_path, TaskBrief("研究问题", "报告后等待 RSI"))
    c.start()
    c.begin_turn("rsi")
    agent = c.spawn(key="one", direction="方向", instruction="检查并报告")["agent_id"]
    c.wait("rsi", "等待研究结果")
    c.end_turn("rsi")
    c.begin_turn(agent)
    c.report(agent, key="report", fields={"message": "检查完成，需要决定是否继续"})
    c.end_turn(agent)
    c.begin_turn("rsi")
    return c, agent


def test_delivered_report_gets_one_reminder_after_restart(tmp_path: Path) -> None:
    c, agent = reported(tmp_path)
    c.end_turn("rsi")  # RSI replied to something else and forgot this report.
    reopened = Coordinator(ResearchStore(tmp_path, c.store.run_id))
    reminders = reopened.reconcile_report_replies()
    assert len(reminders) == 1
    assert reopened.reconcile_report_replies() == []  # Already queued.
    assert reopened.actor(agent).state == AgentState.WAITING
    assert len(reopened.actor(agent).iterations) == 1
    message, _ = reopened.begin_turn("rsi")
    assert message.message_id == reminders[0]
    assert message.sender == "runtime"
    assert message.references
    reopened.end_turn("rsi")  # Even a noncompliant model must not cause infinite wakeups.
    assert Coordinator(reopened.store).reconcile_report_replies() == []
    assert len(reopened.summary()["pending_report_replies"]) == 1
    assert len(model_state(reopened, "rsi")["pending_report_replies"]) == 1
    assert "pending_report_replies" not in model_state(reopened, agent)


@pytest.mark.parametrize("decision", ["assign", "stop", "wait", "user", "job", "pause", "run_stop"])
def test_explicit_decisions_and_controls_do_not_trigger_reminders(
    tmp_path: Path, decision: str
) -> None:
    c, agent = reported(tmp_path)
    if decision == "assign":
        c.assign(agent, key="next", instruction="继续")
    elif decision == "stop":
        c.stop_agent(agent, "已有结论")
    elif decision == "wait":
        c.wait("rsi", "等待另一个方向")
    elif decision == "user":
        c.ask_user("rsi", "需要环境信息", key="question")
    elif decision == "job":
        c.wait("rsi", "job:running")
    elif decision == "pause":
        c.pause()
    else:
        c.stop("用户结束")
    c.end_turn("rsi")
    assert Coordinator(c.store).reconcile_report_replies() == []
    if decision in {"assign", "stop", "run_stop"}:
        assert c.pending_report_replies() == []


def test_explicit_wait_is_reconsidered_when_a_new_event_arrives(tmp_path: Path) -> None:
    c, _ = reported(tmp_path)
    c.wait("rsi", "等待用户补充")
    c.end_turn("rsi")
    c.enqueue("user", "rsi", "信息已经补齐", key="answer")
    c.begin_turn("rsi")
    c.end_turn("rsi")
    assert len(c.reconcile_report_replies()) == 1


@pytest.mark.parametrize("reason", ["job:running", "等待另一个方向", "user"])
def test_legacy_explicit_wait_is_respected_without_new_marker(tmp_path: Path, reason: str) -> None:
    c, _ = reported(tmp_path)
    c.wait("rsi", reason)
    c.end_turn("rsi")
    c.state.pop("rsi_explicit_wait")
    c.save()
    assert Coordinator(c.store).reconcile_report_replies() == []


def test_report_not_delivered_yet_is_not_duplicated(tmp_path: Path) -> None:
    c, _ = reported(tmp_path)
    assert c.reconcile_report_replies() == []  # RSI is still processing the report.
    assert not [m for m in c.store.records("messages", ResearchMessage) if m.sender == "runtime"]
