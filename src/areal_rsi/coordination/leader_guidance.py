"""Optional, targeted advice on top of independent pass@k attempts."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from areal_rsi.agents.research.leader_guidance_prompt import PROMPT as RESEARCH_PROMPT
from areal_rsi.agents.rsi.leader_guidance_prompt import PROMPT as RSI_PROMPT
from areal_rsi.api.base import RecordRef
from areal_rsi.base.storage import now
from areal_rsi.coordination.actions import REFERENCES, STRING, ResearchActions, definition
from areal_rsi.coordination.passk import SUPERVISOR_TOOLS, PassKActions
from areal_rsi.modules.agent_runtime import ToolResult
from areal_rsi.modules.runtime_storage import read_journal

if TYPE_CHECKING:
    from areal_rsi.coordination.collaboration import CollaborationPlan


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

    def _is_fusion(self, agent_id: str) -> bool:
        if agent_id == "rsi":
            return True
        path = self.c.store.root / "coordination/config.json"
        metadata = json.loads(path.read_text()).get("metadata", {}) if path.exists() else {}
        portfolios = metadata.get("research_portfolios")
        if not isinstance(portfolios, list):
            return True
        researchers = [actor.agent_id for actor in self.c.store.run().research_agents]
        return (
            agent_id in researchers and portfolios[researchers.index(agent_id)]["role"] == "fusion"
        )

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
        return (
            tools if agent_id == "interaction" or not self._is_fusion(agent_id) else [*tools, TRACE]
        )

    def can_team_read(self, agent_id: str) -> bool:
        return self._is_fusion(agent_id)

    def read_record(self, agent_id: str, uri: str) -> Any:
        if not self._is_fusion(agent_id):
            return super().read_record(agent_id, uri)
        ref = self.c.store.record_reference(RecordRef(uri))
        return self.c.store.public_record(ref)

    def state(self, agent_id: str) -> dict[str, Any]:
        state = super().state(agent_id)
        if agent_id == "rsi":
            for researcher in state["researchers"]:
                researcher["latest_advice"] = self.c.state.get("leader_advice", {}).get(
                    researcher["agent_id"]
                )
        if agent_id not in {"rsi", "interaction"} and self._is_fusion(agent_id):
            state["researchers"] = [
                {
                    "agent_id": actor.agent_id,
                    "direction": actor.direction,
                    "state": actor.state,
                    "latest_report": self.c.state.get("passk_reports", {}).get(actor.agent_id),
                }
                for actor in self.c.store.run().research_agents
            ]
        elif agent_id not in {"rsi", "interaction"}:
            state["researchers"] = []
        return state

    async def _apply(self, agent_id: str, key: str, name: str, args: dict[str, Any]) -> ToolResult:
        if name in {"advise_researcher", "read_researcher_trace"}:
            if name == "read_researcher_trace" and not self._is_fusion(agent_id):
                raise ValueError("Only the fusion researcher and RSI may read cross-agent traces")
            target = args["agent_id"]
            if target not in {actor.agent_id for actor in self.c.store.run().research_agents}:
                raise ValueError("Unknown researcher")
            if name == "advise_researcher":
                refs = self.handoff_refs(args["message"], args["references"])
                message_id = self.c.enqueue(
                    "rsi",
                    target,
                    args["message"],
                    key=key,
                    references=refs,
                )
                self.c.state.setdefault("leader_advice", {})[target] = {
                    "message_id": message_id,
                    "message": args["message"],
                    "references": [ref.uri for ref in refs],
                }
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
