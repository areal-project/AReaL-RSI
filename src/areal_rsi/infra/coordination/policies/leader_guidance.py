"""Optional, targeted advice on top of independent pass@k attempts."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from areal_rsi.api.interfaces import ToolResult
from areal_rsi.api.records import RecordRef
from areal_rsi.api.research import LeaderAdvice
from areal_rsi.infra.agents.research.leader_guidance_prompt import PROMPT as RESEARCH_PROMPT
from areal_rsi.infra.agents.rsi.leader_guidance_prompt import PROMPT as RSI_PROMPT
from areal_rsi.infra.coordination.policies.passk import SUPERVISOR_TOOLS, PassKActions
from areal_rsi.infra.coordination.tools.actions import (
    REFERENCES,
    STRING,
    ResearchActions,
    definition,
)
from areal_rsi.infra.runtime.runtime_storage import read_journal
from areal_rsi.infra.storage.files import encode, now

if TYPE_CHECKING:
    from areal_rsi.infra.coordination.policies.collaboration import CollaborationPlan


ADVISE = definition(
    "advise_researcher",
    "向一名研究员发送非阻塞指导和原始证据引用，说明下一步、依据和适用条件。"
    "不改变任务、工作区或正在执行的作业；建议可由接收者自行判断是否采纳。",
    agent_id=STRING,
    message=STRING,
    references=REFERENCES,
)
TRACE = definition(
    "read_researcher_trace",
    "查看一名研究员原会话中的消息与工具调用，保存为可引用的原始片段。"
    "offset=-1 读取最近 16000 字符，0 从头读取；按 next_offset 继续。",
    agent_id=STRING,
    offset={"type": "integer", "minimum": -1},
)
LEADER_TOOLS = {
    "update_plan",
    "read_record",
    "list_records",
    "read_candidate_file",
    "job_status",
    "read_job_log",
    "read_job_output_file",
    "search_web",
    "fetch_url",
}
TRACE_EVENTS = {
    "message.completed",
    "tool.started",
    "tool.completed",
    "tool.failed",
    "execution.failed",
}


class GuidedPassKActions(PassKActions):
    team_read = True

    def definitions(self, agent_id: str, *, include_hidden: bool = False) -> list[dict[str, Any]]:
        if agent_id == "rsi":
            return [
                *SUPERVISOR_TOOLS,
                ADVISE,
                TRACE,
                *[
                    tool
                    for tool in ResearchActions.definitions(
                        self, agent_id, include_hidden=include_hidden
                    )
                    if tool["name"] in LEADER_TOOLS
                ],
            ]
        tools = super().definitions(agent_id, include_hidden=include_hidden)
        return tools if agent_id == "interaction" else [*tools, TRACE]

    def read_record(self, agent_id: str, uri: str) -> Any:
        ref = self.c.store.record_reference(RecordRef(uri))
        return self.c.store.public_record(ref)

    def state(self, agent_id: str) -> dict[str, Any]:
        state = super().state(agent_id)
        if agent_id == "rsi":
            for researcher in state["researchers"]:
                researcher["latest_advice"] = encode(
                    self.c.state.leader_advice.get(researcher["agent_id"])
                )
        if agent_id not in {"rsi", "interaction"}:
            state["researchers"] = [
                {
                    "agent_id": actor.agent_id,
                    "direction": actor.direction,
                    "state": actor.state,
                    "latest_report": encode(self.c.state.passk_reports.get(actor.agent_id)),
                }
                for actor in self.c.store.run().research_agents
            ]
        return state

    async def _apply(self, agent_id: str, key: str, name: str, args: dict[str, Any]) -> ToolResult:
        if name in {"advise_researcher", "read_researcher_trace"}:
            target = args["agent_id"]
            if target not in {actor.agent_id for actor in self.c.store.run().research_agents}:
                raise ValueError("Unknown researcher")
            if name == "advise_researcher":
                goal = self.c.state.goals.get(target)
                if goal is not None and goal.status == "completed":
                    raise ValueError("该研究员已完成目标；需要新工作时由用户调整目标")
                refs = self.handoff_refs(args["message"], args["references"])
                message_id = self.c.enqueue(
                    "rsi",
                    target,
                    args["message"],
                    key=key,
                    references=refs,
                )
                self.c.state.leader_advice[target] = LeaderAdvice(
                    message_id, args["message"], [ref.uri for ref in refs]
                )
                self.c.save()
                return ToolResult({"message_id": message_id, "status": "queued"})
            return self.trace(target, key, args["offset"])
        return await super()._apply(agent_id, key, name, args)

    def trace(self, agent_id: str, key: str, offset: int) -> ToolResult:
        store = self.c.store
        actor = self.c.actor(agent_id)
        session = actor.session_id or agent_id
        root = store.paths.agent(agent_id) / "sessions" / session / "runtime/data/state"
        events, _ = read_journal(root, boundary=store.root)
        text = "\n".join(
            json.dumps(event.model_dump(mode="json", by_alias=True), ensure_ascii=False)
            for event in events
            if event.type in TRACE_EVENTS
        )
        start = max(0, len(text) - 16000) if offset == -1 else offset
        value = {
            "agent_id": agent_id,
            "session_id": session,
            "created_at": now(),
            "kind": "research_trace",
            "offset": start,
            "content": text[start : start + 16000],
            "next_offset": start + 16000 if start + 16000 < len(text) else None,
            "total_chars": len(text),
        }
        ref = store.put("materials", key, value)
        return ToolResult({"uri": ref.uri, **value})


def apply(plan: CollaborationPlan) -> CollaborationPlan:
    return replace(
        plan,
        actions=GuidedPassKActions,
        rsi_prompt=RSI_PROMPT,
        research_prompt=RESEARCH_PROMPT,
        supervisor_only=False,
    )
