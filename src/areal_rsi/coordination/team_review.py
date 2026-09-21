"""Occasional team review, using the existing nonblocking leader inbox."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from areal_rsi.api.base import AgentState
from areal_rsi.coordination.passk import PassKEngine
from areal_rsi.prompts import load_prompt

if TYPE_CHECKING:
    from areal_rsi.coordination.collaboration import CollaborationPlan


class TeamReviewEngine(PassKEngine):
    review_interval_seconds = 600

    def _remind_team_review(self) -> None:
        c = self.coordinator
        if (
            c.state["status"] != "running"
            or not c.state.get("passk_ready")
            or self._wall_reason() is not None
            or not any(a.state != AgentState.STOPPED for a in c.store.run().research_agents)
        ):
            return
        at = self.clock()
        review = c.state.get("team_review")
        if review is None:
            c.state["team_review"] = {"next_at": at + self.review_interval_seconds}
            c.save()
            return
        if at < review["next_at"]:
            return
        previous = review.get("message_id")
        if previous and previous not in c.state["delivered"]:
            return  # Keep at most one unseen reminder, including during a long native turn.
        review["next_at"] = at + self.review_interval_seconds
        review["message_id"] = c.enqueue(
            "system",
            "rsi",
            load_prompt("agents/rsi/team_review_reminder.md"),
            key=f"team-review:{at}",
        )
        c.save()

    async def tick(self) -> int:
        self._acquire()
        self._remind_team_review()
        return await super().tick()


def apply(plan: CollaborationPlan) -> CollaborationPlan:
    return replace(
        plan,
        engine=type("ReviewedTeamEngine", (TeamReviewEngine, plan.engine), {}),
        rsi_prompt=plan.rsi_prompt.replace(
            "{{tool_instructions}}",
            load_prompt("agents/rsi/team_review.md") + "\n\n{{tool_instructions}}",
        ),
    )
