"""Structured agent controls with phase authorization and durable call receipts."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from areal_rsi.api.control import (
    GoalUpdate,
    StartGoal,
    TaskProposal,
    decode_record,
    record_schema,
)
from areal_rsi.api.research import (
    GoalProgress,
    ResearchLimits,
    SessionPhase,
    SessionRecord,
    SessionStatus,
    TaskBrief,
    ToolReceipt,
)


def definitions() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": name,
            "description": description,
            "inputSchema": record_schema(kind),
        }
        for name, description, kind in (
            (
                "rsi_set_task",
                "Interaction only: save the discussed objective and acceptance criteria.",
                TaskProposal,
            ),
            (
                "rsi_start_goal",
                "Interaction only: hand off the prepared task after user authorization.",
                StartGoal,
            ),
            (
                "rsi_update_goal",
                "Leader only: record progress, evidence-based completion or a blocker.",
                GoalUpdate,
            ),
        )
    ]


def apply_control(record: SessionRecord, key: str, name: str, arguments: Any) -> ToolReceipt:
    digest = hashlib.sha256(json.dumps(arguments, sort_keys=True).encode()).hexdigest()
    if key in record.receipts:
        receipt = record.receipts[key]
        if receipt.tool != name or receipt.arguments_digest != digest:
            raise ValueError("A tool call ID cannot be reused with different arguments")
        return receipt
    if record.status != SessionStatus.RUNNING:
        raise ValueError("Agent controls require a running turn")
    if name in {"rsi_set_task", "rsi_start_goal"}:
        if record.phase != SessionPhase.INTERACTION or record.handoff_pending:
            raise ValueError("Only the current Interaction may prepare and hand off a goal")
        if name == "rsi_set_task":
            proposal = decode_record(TaskProposal, arguments)
            if not proposal.instructions.strip() or not proposal.acceptance:
                raise ValueError("Task instructions and acceptance criteria are required")
            record.goal = TaskBrief(
                proposal.objective,
                proposal.instructions,
                record.goal.limits if record.goal else ResearchLimits(),
                acceptance=proposal.acceptance,
            )
            text = (
                "Task saved. Discuss remaining questions or start when the user has authorized it."
            )
        else:
            decode_record(StartGoal, arguments)
            if record.goal is None:
                raise ValueError("Save a task before starting the goal")
            record.handoff_pending = True
            text = (
                "Handoff scheduled after this turn. Finish your reply; the Leader inherits history."
            )
    elif name == "rsi_update_goal":
        if record.phase != SessionPhase.LEADER or record.goal is None:
            raise ValueError("Only the executing Leader may update the goal")
        update = decode_record(GoalUpdate, arguments)
        if not update.summary.strip():
            raise ValueError("A progress summary is required")
        if update.status == "completed" and (
            not update.evidence or any(not item.strip() for item in update.evidence)
        ):
            raise ValueError("Completion requires concrete validation evidence")
        if update.status == "blocked" and not update.question.strip():
            raise ValueError("Explain the blocker and what user input is needed")
        record.progress = GoalProgress(
            update.summary, update.next_step, update.evidence, update.status, update.question
        )
        text = "Progress saved. " + (
            "Finish the current reply; the host will stop automatic continuation."
            if update.status != "active"
            else "Continue toward the goal."
        )
    else:
        raise ValueError(f"Unknown RSI tool: {name}")
    receipt = ToolReceipt(name, digest, text, True)
    record.receipts[key] = receipt
    return receipt
