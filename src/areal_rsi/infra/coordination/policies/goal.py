"""Goal policy at idle boundaries; one native Codex turn executes at a time."""

from areal_rsi.api.research import SessionPhase, SessionRecord, SessionStatus


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
