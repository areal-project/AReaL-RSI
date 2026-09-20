"""A fresh conversation in the same research slot, with its original clock."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from areal_rsi.api.base import AgentState
from areal_rsi.api.research import CandidateSnapshot, ResearchMessage
from areal_rsi.base.storage import now
from areal_rsi.coordination.actions import STRING, definition
from areal_rsi.coordination.continuous_guidance import (
    ContinuousGuidanceActions,
    ContinuousGuidanceEngine,
)
from areal_rsi.coordination.coordinator import effect_id
from areal_rsi.modules.agent_runtime import ToolResult
from areal_rsi.prompts import load_prompt

if TYPE_CHECKING:
    from areal_rsi.coordination.collaboration import CollaborationPlan


PENDING = {"queued", "archived", "restored"}
RESTART = definition(
    "restart_researcher",
    "为停滞的研究员申请回到自己历史候选或公开起点，并开启新会话。预算不重置。"
    "先用 advise_researcher 给他一条简明的新起点说明，再引用该 message_id。"
    "系统先结束旧会话、收齐在途作业，再保存工作区并切换；可用 get_state 跟踪。",
    agent_id=STRING,
    candidate_id=STRING,
    reason=STRING,
    handoff_message_id=STRING,
)


class RestartActions(ContinuousGuidanceActions):
    def definitions(self, agent_id: str, *, include_hidden: bool = False) -> list[dict[str, Any]]:
        tools = super().definitions(agent_id, include_hidden=include_hidden)
        tools = [
            {
                **tool,
                "parameters": {
                    **tool["parameters"],
                    "properties": {
                        **tool["parameters"]["properties"],
                        "session_id": {
                            **STRING,
                            "description": "空字符串表示当前会话；"
                            "历史会话使用 get_state 返回的 session_id。",
                        },
                    },
                    "required": [*tool["parameters"]["required"], "session_id"],
                },
            }
            if tool["name"] == "read_researcher_trace"
            else tool
            for tool in tools
        ]
        return [*tools, RESTART] if agent_id == "rsi" else tools

    def state(self, agent_id: str) -> dict[str, Any]:
        state = super().state(agent_id)
        requests = list(self.c.state.get("researcher_restarts", {}).values())
        state["restarts"] = (
            requests
            if agent_id == "rsi"
            else [request for request in requests if request["agent_id"] == agent_id]
        )
        for researcher in state.get("researchers", []):
            identifier = researcher["agent_id"]
            researcher["session_id"] = self.c.actor(identifier).session_id
            researcher["previous_sessions"] = [
                request["old_session_id"]
                for request in requests
                if request["agent_id"] == identifier and request["status"] == "completed"
            ]
        return state

    async def _apply(self, agent_id: str, key: str, name: str, args: dict[str, Any]) -> ToolResult:
        c = self.c
        if name == "read_researcher_trace" and args.get("session_id"):
            target, session = args["agent_id"], args["session_id"]
            known = {actor.agent_id for actor in c.store.run().research_agents}
            if target not in known:
                raise ValueError("Unknown researcher")
            allowed = {c.actor(target).session_id} | {
                r["old_session_id"]
                for r in c.state.get("researcher_restarts", {}).values()
                if r["agent_id"] == target
            }
            if session not in allowed:
                raise ValueError("会话不属于当前 run 中该研究员的记录")
            return self.trace(target, key, args["offset"], session_id=session)
        if name != "restart_researcher":
            return await super()._apply(agent_id, key, name, args)
        target = args["agent_id"]
        researchers = c.store.run().research_agents
        if target not in {actor.agent_id for actor in researchers}:
            raise ValueError("Unknown researcher")
        requests = c.state.get("researcher_restarts", {})
        used = [request for request in requests.values() if request["status"] != "cancelled"]
        if any(request["status"] in PENDING for request in used):
            raise ValueError("请先等当前重启完成，再决定下一个")
        if len(used) >= len(researchers) // 2 or any(r["agent_id"] == target for r in used):
            raise ValueError("本次最多重启一半研究员，每人一次")
        budget = c.budget().get("researchers", {}).get(target)
        if not budget or budget["remaining_effective_seconds"] < budget["limit_seconds"] / 3:
            raise ValueError("重启至少需要剩余三分之一的个人有效预算")
        if c.actor(target).state == AgentState.STOPPED or c.state["status"] != "running":
            raise ValueError("研究员已停止")
        best = c.store.run().best_result_ref
        if (
            best
            and c.store.get(
                "candidates", c.store.public_record(best)["candidate_id"], CandidateSnapshot
            ).source_agent_id
            == target
        ):
            raise ValueError("保留当前最佳研究员，优先审视重复且停滞的其他路线")
        candidate = self.candidate(target, args["candidate_id"])
        handoff = c.store.get("messages", args["handoff_message_id"], ResearchMessage)
        if handoff.sender != "rsi" or handoff.recipient != target:
            raise ValueError("请引用 RSI 发给这名研究员的新起点说明")
        if not args["reason"].strip():
            raise ValueError("请说明原始证据支持的停滞及重启理由")
        request = {
            **args,
            "request_id": key,
            "created_at": now(),
            "status": "queued",
            "old_session_id": c.actor(target).session_id or target,
            "new_session_id": effect_id("session", key),
            "budget_at_request": budget,
            "candidate_id": candidate.candidate_id,
        }
        c.state.setdefault("researcher_restarts", {})[key] = request
        c.save()
        c.store.event("researcher_restart_requested", **request)
        return ToolResult({"status": "queued", "request_id": key, "budget": budget})


class RestartEngine(ContinuousGuidanceEngine):
    state_actions = RestartActions

    def _actor_allowed(self, agent_id: str) -> bool:
        pending = any(
            request["agent_id"] == agent_id and request["status"] in PENDING
            for request in self.coordinator.state.get("researcher_restarts", {}).values()
        )
        return not pending and super()._actor_allowed(agent_id)

    async def tick(self) -> int:
        self._acquire()
        if self.coordinator.state["status"] == "running":
            for request in self.coordinator.state.get("researcher_restarts", {}).values():
                if request["status"] in PENDING:
                    await self._restart(request)
        return await super().tick()

    async def _restart(self, request: dict[str, Any]) -> None:
        c, target, key = self.coordinator, request["agent_id"], request["request_id"]
        task = self._tasks.get(target)
        if task is not None and not task.done():
            return  # ResearchEngine.tick cancels this actor before the next tick.
        if c.actor(target).state == AgentState.STOPPED:
            request.update(status="cancelled", cancellation_reason="researcher_stopped")
            c.save()
            return
        old_session = c.store.paths.agent(target) / "sessions" / request["old_session_id"]
        await self.runtime.close_session(old_session)
        if self.effective:
            self.effective.availability(target, True, self.clock())
        jobs_path = c.store.root / "coordination/jobs.json"
        jobs = json.loads(jobs_path.read_text()) if jobs_path.exists() else {}
        if any(job["agent_id"] == target and not job["processed"] for job in jobs.values()):
            return  # Frozen old inputs may finish; no old tool can alter the new workspace.
        actions = self.state_actions(c)
        if request["status"] == "queued":
            best = c.store.run().best_result_ref
            newly_best = (
                best
                and c.store.get(
                    "candidates", c.store.public_record(best)["candidate_id"], CandidateSnapshot
                ).source_agent_id
                == target
            )
            budget = c.budget()["researchers"][target]
            if newly_best or budget["remaining_effective_seconds"] < budget["limit_seconds"] / 3:
                request.update(
                    status="cancelled",
                    cancellation_reason="new_best" if newly_best else "remaining_budget",
                )
                c.save()
                c.enqueue(
                    "system", target, "本次重启已取消，继续原来的完整研究。", key=key + ":cancel"
                )
                return
            archived_id = effect_id("candidate", key + ":before-restart")
            path = c.store.root / "candidates" / (archived_id + ".json")
            if not path.exists():
                await c.store.snapshot(
                    actions.workspace(target),
                    agent_id=target,
                    description="重启前的完整工作区",
                    candidate_id=archived_id,
                    parent_candidate_id=actions.workspace_candidate(target),
                    scope_base_candidate_id=c.state["seed_candidate_id"],
                )
            actions.mark_workspace_candidate(target, archived_id)
            request.update(status="archived", archived_candidate_id=archived_id)
            c.save()
        if request["status"] == "archived":
            candidate = c.store.get("candidates", request["candidate_id"], CandidateSnapshot)
            await actions.checkout_candidate(target, candidate, key)
            request["status"] = "restored"
            c.save()
        handoff = c.store.get("messages", request["handoff_message_id"], ResearchMessage)
        request.setdefault(
            "discarded_message_ids",
            list(
                dict.fromkeys(
                    [
                        *c.state["inboxes"].get(target, []),
                        *c.current_message_ids(target),
                    ]
                )
            ),
        )
        for field in (
            "active",
            "turn_followups",
            "turn_retries",
            "turn_failures",
            "passk_retries",
            "passk_reports",
            "research_progress",
            "agent_activity",
            "leader_advice",
        ):
            c.state.get(field, {}).pop(target, None)
        c.state["inboxes"][target] = []
        actor = c.actor(target)
        actor.session_id = request["new_session_id"]
        actor.state, actor.wait_reason, actor.error = AgentState.WAITING, "restart", None
        c.save_actor(actor)
        brief = c.store.brief()
        text = (
            load_prompt("agents/research/restart_task.md")
            .replace("{{objective}}", brief.objective)
            .replace("{{task}}", brief.instructions)
            .replace("{{candidate_id}}", request["candidate_id"])
            .replace("{{handoff}}", handoff.text)
        )
        c.enqueue("rsi", target, text, key=key + ":fresh-task", references=handoff.references)
        request.update(
            status="completed",
            completed_at=now(),
            budget_after_restart=c.budget()["researchers"][target],
        )
        c.save()
        c.store.event("researcher_restarted", **request)
        c.enqueue(
            "system",
            "rsi",
            f"{target} 已从 {request['candidate_id']} 开始新会话；仍使用原个人预算。",
            key=key + ":completed",
        )


def apply(plan: CollaborationPlan) -> CollaborationPlan:
    actions = type("RestartableActions", (RestartActions, plan.actions), {})
    return replace(
        plan,
        actions=actions,
        engine=type(
            "RestartableEngine", (RestartEngine, plan.engine),
            {"state_actions": actions},
        ),
        rsi_prompt=plan.rsi_prompt.replace(
            "{{tool_instructions}}",
            load_prompt("agents/rsi/researcher_restart.md") + "\n\n{{tool_instructions}}",
        ),
    )
