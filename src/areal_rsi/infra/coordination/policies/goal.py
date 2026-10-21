"""RSI phase transitions and continuation policy; no transport or persistence."""

import uuid

from areal_rsi.api.research import (
    GoalProgress,
    SessionInput,
    SessionPhase,
    SessionRecord,
    SessionStatus,
)
from areal_rsi.infra.coordination.policies.prompts import EXECUTE


def enter_interaction(record: SessionRecord) -> None:
    if record.phase != SessionPhase.CHAT or record.next_phase is not None:
        raise ValueError("当前已在 RSI 流程中；/chat 可返回普通模式")
    if record.status not in {SessionStatus.IDLE, SessionStatus.RUNNING}:
        raise ValueError("先 /resume 恢复或 /chat 返回普通模式，再进入 RSI")
    record.next_phase = SessionPhase.INTERACTION


def start(record: SessionRecord) -> None:
    if record.phase != SessionPhase.INTERACTION or record.goal is None:
        raise ValueError("请先 /rsi 讨论并保存任务配置")
    if record.status not in {SessionStatus.IDLE, SessionStatus.RUNNING}:
        raise ValueError("请先恢复需求讨论")
    record.handoff_pending = True


def apply_phase(record: SessionRecord) -> bool:
    """Apply a queued switch only at an eligible turn boundary."""
    if record.next_phase is None or (
        record.status != SessionStatus.IDLE and record.next_phase != SessionPhase.CHAT
    ):
        return False
    record.phase, record.next_phase = record.next_phase, None
    record.goal = None
    record.progress = GoalProgress()
    record.in_flight = None
    record.last_error = ""
    record.empty_continuations = 0
    record.status = SessionStatus.IDLE
    return True


def accept_handoff(record: SessionRecord, thread_id: str) -> None:
    """Keep native history and begin the Leader goal with fresh usage counters."""
    assert record.goal is not None
    if record.phase != SessionPhase.LEADER:
        record.source_thread_id, record.thread_id = record.thread_id, thread_id
        record.thread_tokens[thread_id] = record.thread_tokens.get(record.source_thread_id, 0)
        record.phase = SessionPhase.LEADER
        record.tokens_used = 0
        record.seconds_used = 0
        record.progress = GoalProgress()
    record.handoff_pending = False
    record.pending_inputs.insert(0, SessionInput(uuid.uuid4().hex, EXECUTE))


def budget_exhausted(record: SessionRecord) -> bool:
    if record.phase != SessionPhase.LEADER or record.goal is None:
        return False
    limits = record.goal.limits
    return (
        limits.max_total_tokens is not None and record.tokens_used >= limits.max_total_tokens
    ) or (limits.max_wall_seconds is not None and record.seconds_used >= limits.max_wall_seconds)


def settle(record: SessionRecord, *, automatic: bool, did_work: bool) -> None:
    """Resolve a completed turn without claiming that an ordinary reply completes a goal."""
    record.status = SessionStatus.IDLE
    if record.phase != SessionPhase.LEADER:
        return
    if record.progress.decision == "completed":
        record.status = SessionStatus.COMPLETED
    elif record.progress.decision == "blocked":
        record.status = SessionStatus.WAITING
    elif budget_exhausted(record):
        record.status = SessionStatus.BUDGET_LIMITED
    else:
        record.empty_continuations = (
            record.empty_continuations + 1 if automatic and not did_work else 0
        )
        if record.empty_continuations >= 2:
            record.status = SessionStatus.WAITING
            record.progress.question = (
                "连续两次自动继续没有执行工作，已停止空转。请补充方向或 /resume。"
            )


def should_continue(record: SessionRecord) -> bool:
    return (
        record.phase == SessionPhase.LEADER
        and record.status == SessionStatus.IDLE
        and record.progress.decision == "active"
        and not record.pending_inputs
        and record.next_phase is None
        and not record.active_turn_id
        and not budget_exhausted(record)
    )
