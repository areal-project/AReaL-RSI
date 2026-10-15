"""A persistent Leader that executes its own goal and owns run acceptance."""

from __future__ import annotations

from typing import Any

from areal_rsi.api.control import validate_record
from areal_rsi.api.interfaces import ToolResult
from areal_rsi.api.records import AgentState, RunState
from areal_rsi.api.research import GoalContext, ResearchMessage, RunAcceptance
from areal_rsi.infra.agents.communication import INTEGRITY_PROMPT
from areal_rsi.infra.agents.leader import PROMPT
from areal_rsi.infra.coordination.engine import ResearchEngine
from areal_rsi.infra.coordination.tools.actions import STRING, ResearchActions, definition
from areal_rsi.infra.storage.files import decode, encode, now

UPDATE_GOAL = definition(
    "update_goal",
    "记录持续目标的进展、阻塞或最终验收；只有 Leader 可以完成整个 run。",
    status={"type": "string", "enum": ["active", "blocked", "completed"]},
    summary=STRING,
    current_step=STRING,
    next_steps={"type": "array", "items": STRING},
    acceptance={"type": "array", "items": STRING},
    evidence={"type": "array", "items": STRING},
    blocker=STRING,
    candidate_id=STRING,
)


class GoalActions(ResearchActions):
    """Execution tools and one goal tool; delegation is deliberately unavailable."""

    def recover_acceptance(self) -> bool:
        """The immutable decision is the commit point; repair derived state after a crash."""
        c = self.c
        if not (c.store.root / "acceptance/run.json").exists():
            return False
        receipt = c.store.get("acceptance", "run", RunAcceptance)
        goal = receipt.goal
        changed = c.state.goal != goal or c.state.status != "succeeded"
        c.state.goal = goal
        c.state.status = "succeeded"
        c.state.selected_candidate_id = goal.candidate_id or None
        c.state.waiting_for_user = []
        c.save()
        run = c.store.run()
        run.state, run.finished_at = RunState.SUCCEEDED, goal.updated_at
        run.final_result_ref = receipt.final_result_ref
        c.store.save_run(run)
        c.enqueue("rsi", "user", goal.summary, key="goal-acceptance")
        if changed:
            c.store.event("goal_updated", agent_id="rsi", goal=encode(goal))
        return True

    def definitions(self, agent_id: str, *, include_hidden: bool = False) -> list[dict[str, Any]]:
        tools = super().definitions(agent_id, include_hidden=include_hidden)
        if agent_id == "interaction":
            return tools
        if agent_id != "rsi":
            return []
        return [tool for tool in tools if tool["name"] != "wait"] + [UPDATE_GOAL]

    async def _apply(
        self,
        agent_id: str,
        key: str,
        name: str,
        args: dict[str, Any],
    ) -> ToolResult:
        c = self.c
        if name == "ask_user" and agent_id == "rsi":
            result = await super()._apply(agent_id, key, name, args)
            assert c.state.goal is not None
            c.state.goal.status, c.state.goal.blocker = "blocked", args["question"]
            c.save()
            return result
        if name != "update_goal":
            return await super()._apply(agent_id, key, name, args)
        if agent_id == "rsi" and self.recover_acceptance():
            return ToolResult({"completed": True}, stop=True, reason="completed")
        if agent_id != "rsi" or c.state.status != "running":
            raise ValueError("Only the running Leader can update the run goal")
        validate_record(GoalContext, args)
        goal: GoalContext = decode(GoalContext, args)
        if not goal.summary.strip():
            raise ValueError("Goal updates require a progress summary")
        if goal.status == "blocked" and not goal.blocker.strip():
            raise ValueError("Describe the blocker and what is needed to continue")
        goal.revision = (c.state.goal.revision if c.state.goal else 0) + 1
        goal.updated_at = now()
        if goal.status == "completed":
            if not goal.acceptance or not all(s.strip() for s in goal.acceptance):
                raise ValueError("Completion requires explicit acceptance criteria")
            if not goal.evidence or not all(s.strip() for s in goal.evidence):
                raise ValueError("Completion requires actual evidence")
            if any(not job.processed for job in c.store.research_jobs().values()):
                raise ValueError("Wait for all managed jobs and collect their results first")
            if any(
                identifier not in c.current_message_ids(agent_id)
                and c.store.get("messages", identifier, ResearchMessage).sender == "user"
                for identifier in c.state.inboxes.get(agent_id, [])
            ):
                raise ValueError("Handle pending user instructions before acceptance")
            run, selected = self.completion_candidate(
                goal.candidate_id or None,
                require_verification=True,
            )
            goal.candidate_id = selected or ""
            c.store.put(
                "acceptance",
                "run",
                RunAcceptance(
                    goal,
                    c.store.brief(),
                    run.final_result_ref,
                    tuple(
                        m
                        for m in c.store.records("messages", ResearchMessage)
                        if m.sender == "user"
                    ),
                ),
            )
            self.recover_acceptance()
            return ToolResult({"completed": True}, stop=True, reason="completed")
        elif goal.status == "blocked":
            c.ask_user(agent_id, goal.blocker, key=key + "-blocker")
            result = ToolResult({"goal": encode(goal)}, stop=True, reason="user")
        else:
            result = ToolResult({"goal": encode(goal)})
        c.state.goal = goal
        c.save()
        c.store.event("goal_updated", agent_id=agent_id, goal=encode(goal))
        return result


class GoalEngine(ResearchEngine):
    rsi_prompt = PROMPT + "\n" + INTEGRITY_PROMPT

    async def tick(self) -> int:
        self._acquire()
        c = self.coordinator
        actor = c.actor("rsi")
        if (
            c.state.status == "running"
            and c.state.goal
            and c.state.goal.status == "active"
            and actor.state == AgentState.WAITING
            and actor.wait_reason == "message"
            and "rsi" not in c.state.active
            and not c.state.inboxes.get("rsi")
            and "rsi" not in self.active_agents
        ):
            c.enqueue(
                "system",
                "rsi",
                "目标尚未完成。根据已保存的进展继续执行和验证。",
                key=f"goal-continue-{c.state.runtime_turns}-{len(c.state.delivered)}",
            )
        return await super().tick()

    def _actor_allowed(self, agent_id: str) -> bool:
        c = self.coordinator
        if agent_id == "interaction":
            return c.state.status == "planned"
        return (
            agent_id == "rsi"
            and c.state.status == "running"
            and c.state.goal is not None
            and c.state.goal.status == "active"
        )
