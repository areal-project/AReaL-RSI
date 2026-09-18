"""Optional, targeted advice on top of independent pass@k attempts."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from areal_rsi.agents.communication import INTEGRITY_PROMPT
from areal_rsi.api.base import RecordRef
from areal_rsi.api.research import ResearchMessage
from areal_rsi.base.storage import now
from areal_rsi.coordination.actions import REFERENCES, STRING, ResearchActions, definition
from areal_rsi.coordination.passk import SUPERVISOR_TOOLS, PassKActions
from areal_rsi.modules.agent_runtime import ToolResult
from areal_rsi.modules.runtime_storage import read_journal

if TYPE_CHECKING:
    from areal_rsi.coordination.collaboration import CollaborationPlan


RSI_PROMPT = """你是 RSI Agent，是整个 agents 团队的 leader。

用 start_researchers 启动 {{max_agents}} 名研究员，让每人独立完成整个任务，
自行调研、实现、验证和持续改进。不拆分任务，也不要求他们等待你的决定。

关注报告、正式结果和遇到的困难。需要判断时，查看原始记录、候选代码或研究轨迹。
发现对某位研究员有帮助的经验时，用 advise_researcher 定向分享，并附上原始记录引用。
简要说明依据、适用条件和不确定之处；把已验证的事实与自己的推测分开。
给出可供尝试的建议，让接收者自行判断并验证，不要求所有人跟随当前最优方案。

只在有具体帮助时发消息，不重复催问普通进度。提醒已停下来的研究员继续探索；
不打断正在进行的实验，不替换、停止研究员或覆盖他们的代码。
没有需要处理的事项时用 wait 等待新报告。遵守系统的目标和预算。

{{tool_instructions}}

不获取或提供题目的现成答案，不作弊，不伪造或隐瞒结果。
"""

RESEARCH_PROMPT = """你是 Research Agent，独立负责完成整个研究任务。

先理解任务、起始代码和评测方法。开始时主动联网调研，后续按需要继续调研。
自行提出方案、实现、验证并持续改进，可以自由更换方向。
保存有价值的版本，以正式验证结果判断正确性和目标指标。

首次通过正确性、有实质改进或遇到持续困难时，向 RSI 简要报告，附上相关记录引用。
报告后继续研究，不等待回复。RSI 可能分享其他研究员的经验或建议；
结合原始证据自行判断，采纳后仍需自己验证，不因收到建议就放弃正在验证的思路。
可以用 read_record 阅读定向共享的记录，用 read_shared_file 查看共享候选中的文件。
只操作自己的工作区；其他尝试的资料只能通过 RSI 明确共享的引用读取。

提交一个版本、完成报告或暂时受阻都不代表任务结束。在系统通知停止前持续探索。

{{tool_instructions}}

""" + INTEGRITY_PROMPT.replace("如实报告并撤回", "如实记录并撤回")

ADVISE = definition(
    "advise_researcher",
    "向一名研究员发送非阻塞建议和原始证据引用；仅此接收者获得引用的读取权限。"
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
SHARED_FILE = definition(
    "read_shared_file",
    "读取 RSI 定向共享的 candidates 记录中的文件，不导入或切换工作区。"
    "uri 使用共享记录引用，path 为空时列出文件。",
    uri=STRING,
    path=STRING,
)
LEADER_READ_TOOLS = {
    "read_record",
    "list_records",
    "read_candidate_file",
    "job_status",
    "read_job_log",
    "read_job_output_file",
    "search_web",
    "fetch_url",
}
TRACE_EVENTS = {"message.completed", "tool.started", "tool.completed", "execution.failed"}


class GuidedPassKActions(PassKActions):
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
                    if tool["name"] in LEADER_READ_TOOLS
                ],
            ]
        tools = super().definitions(agent_id, include_hidden=include_hidden)
        return tools if agent_id == "interaction" else [*tools, SHARED_FILE]

    def shared_references(self, agent_id: str) -> set[str]:
        # The durable message itself is the grant; no second permission store.
        return {
            ref.uri
            for message in self.c.store.records("messages", ResearchMessage)
            if message.sender == "rsi" and message.recipient == agent_id
            for ref in message.references
        }

    def read_record(self, agent_id: str, uri: str) -> Any:
        ref = self.c.store.record_reference(RecordRef(uri))
        if agent_id == "rsi" or ref.uri in self.shared_references(agent_id):
            return self.c.store.public_record(ref)
        return super().read_record(agent_id, ref.uri)

    def state(self, agent_id: str) -> dict[str, Any]:
        state = super().state(agent_id)
        if agent_id not in {"rsi", "interaction"}:
            state["shared_records"] = sorted(self.shared_references(agent_id))
        return state

    async def _apply(self, agent_id: str, key: str, name: str, args: dict[str, Any]) -> ToolResult:
        if name in {"advise_researcher", "read_researcher_trace"}:
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
                return ToolResult({"message_id": message_id, "status": "queued"})
            return self.trace(target, key, args["offset"])
        if name == "read_shared_file":
            ref = self.c.store.record_reference(RecordRef(args["uri"]))
            if urlsplit(ref.uri).path.split("/")[1] != "candidates":
                raise ValueError("Use a shared candidates record")
            candidate = self.read_record(agent_id, ref.uri)
            return await ResearchActions._apply(
                self,
                agent_id,
                key,
                "read_candidate_file",
                {"candidate_id": candidate["candidate_id"], "path": args["path"]},
            )
        return await super()._apply(agent_id, key, name, args)

    def trace(self, agent_id: str, key: str, offset: int) -> ToolResult:
        store = self.c.store
        actor = self.c.actor(agent_id)
        session = actor.session_id or agent_id
        root = store.paths.agent(agent_id) / "sessions" / session / "runtime/data/state"
        events, _ = read_journal(root)
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
