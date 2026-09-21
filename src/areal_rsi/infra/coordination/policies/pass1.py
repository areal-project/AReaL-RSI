"""Single-Agent goals and durable public context for each Agent turn."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from areal_rsi.api.control import validate_record
from areal_rsi.api.interfaces import ToolResult
from areal_rsi.api.records import AgentState
from areal_rsi.api.research import GoalContext, GoalTurn, ResearchMessage
from areal_rsi.infra.agents.communication import INTEGRITY_PROMPT
from areal_rsi.infra.coordination.engine import ResearchEngine
from areal_rsi.infra.coordination.tools.actions import STRING, ResearchActions, definition
from areal_rsi.infra.storage.files import decode, encode, now

PROMPT = (
    """你是 RSI pass1，一个持续执行目标的 Agent。你自己完成用户目标，不创建或委派其他 Agent。
当前任务的目标、约束和工作区是你的执行范围。按需使用原生工具和 rsi-tools；先读取 get_state。
使用 update_goal 保存给用户看的执行上下文：简要进展、当前步骤、后续步骤、验收条件和实际证据。
开始执行时、重要步骤完成后、发现阻塞时都更新。写事实与简明计划，不写私有思维链。
一次模型回复结束不代表目标完成；系统会让你在同一会话继续，避免重复已经完成的操作。
能独立解决的问题继续处理；确实缺少用户输入或外部条件时，以 blocked 状态写清 blocker 并停止空转。
用户的新指令优先纳入后续工作；通过工具边界收到的新消息应及时处理。
完成前逐项核对验收条件，记录具体测试命令、结果和交付物。代码产物先 save_candidate。
仅在全部工作完成后使用 update_goal 的 completed 状态，必须提供验收条件和证据；
如配置了正式 verifier，先满足其验证要求。不要把自然语言自述当作独立验证器的结果。
需要正式作业时使用已配置的 run_job / verify_candidate。无须为普通目标强行构建评测器。
遇到工具调用错误时修正调用；不要伪造成功，也不要在进度回复后放弃目标。
{{tool_instructions}}
"""
    + "\n"
    + INTEGRITY_PROMPT
)


class Pass1Actions(ResearchActions):
    def definitions(self, agent_id: str, *, include_hidden: bool = False) -> list[dict[str, Any]]:
        if agent_id != "rsi":
            return []
        allowed = {
            "get_state",
            "read_record",
            "list_records",
            "read_candidate_file",
            "import_candidate",
            "list_files",
            "read_file",
            "write_file",
            "save_evidence",
            "save_candidate",
            "checkout_candidate",
            "withdraw_candidate",
        } | (
            self.extras.keys()
            - {
                "handoff_research",
                "freeze_execution",
                "get_setup",
                "inspect_environment",
                "set_execution",
                "load_execution_config",
                "get_adapter_guide",
                "prepare_adapter",
                "prepare_research",
                "start_research",
            }
        )
        return [
            tool
            for tool in super().definitions(agent_id, include_hidden=include_hidden)
            if tool["name"] in allowed
        ] + [
            definition(
                "update_goal",
                "保存公开目标上下文；完成时提供验收条件和证据，阻塞时说明原因。",
                status={"type": "string", "enum": ["active", "blocked", "completed"]},
                summary=STRING,
                current_step=STRING,
                next_steps={"type": "array", "items": STRING},
                acceptance={"type": "array", "items": STRING},
                evidence={"type": "array", "items": STRING},
                blocker=STRING,
                candidate_id=STRING,
            )
        ]

    async def _apply(self, agent_id: str, key: str, name: str, args: dict[str, Any]) -> ToolResult:
        if name != "update_goal":
            return await super()._apply(agent_id, key, name, args)
        if self.c.state.status != "running":
            raise ValueError("Goal updates require a running task")
        value = {k: v for k, v in args.items() if k != "candidate_id"}
        validate_record(GoalContext, value)
        goal: GoalContext = decode(GoalContext, value)
        if not goal.summary.strip():
            raise ValueError("请提供简明进展")
        if goal.status == "blocked" and not goal.blocker.strip():
            raise ValueError("请说明具体阻塞与恢复条件")
        if goal.status == "completed":
            if any(
                identifier != self.c.state.active.get(agent_id)
                and self.c.store.get("messages", identifier, ResearchMessage).sender == "user"
                for identifier in self.c.state.inboxes.get(agent_id, [])
            ):
                raise ValueError("请先处理未送达的用户指令，再完成目标")
            if not any(x.strip() for x in goal.acceptance) or not any(
                x.strip() for x in goal.evidence
            ):
                raise ValueError("完成前必须记录验收条件和实际证据")
            await super()._apply(
                agent_id,
                key,
                "finish",
                {
                    "summary": goal.summary,
                    "candidate_id": args.get("candidate_id", ""),
                },
            )
        goal.updated_at = now()
        goal.revision = (self.c.state.goal.revision if self.c.state.goal else 0) + 1
        self.c.state.goal = goal
        self.c.save()
        self.c.store.event(
            "goal_updated", agent_id=agent_id, goal=encode(goal), summary=goal.summary
        )
        if goal.status == "blocked":
            self.c.wait(agent_id, goal.blocker)
            self.c.enqueue("rsi", "user", goal.blocker, key=key + ":blocked")
        return ToolResult({"goal": encode(goal)}, stop=goal.status != "active", reason=goal.status)


class Pass1Engine(ResearchEngine):
    _goal_turn: GoalTurn | None = None
    _running_tools: dict[str, str]

    def _actor_allowed(self, agent_id: str) -> bool:
        return self.coordinator.state.status == "running" and agent_id == "rsi"

    def _recovery_state(self, agent_id: str) -> dict[str, Any]:
        state = super()._recovery_state(agent_id)
        if self._goal_turn is None:
            self._running_tools = {}
            c = self.coordinator
            c.state.goal_turn_count += 1
            self._goal_turn = GoalTurn(
                c.state.goal_turn_count,
                now(),
                c.store.brief(),
                [
                    c.store.get("messages", key, ResearchMessage)
                    for key in c.current_message_ids(agent_id)
                ],
                deepcopy(c.state.goal),
                c.state.total_tokens,
            )
            c.save()
            self._save_turn()
        return state

    def _save_turn(self) -> None:
        if (turn := self._goal_turn) is not None:
            turn.context_after = deepcopy(self.coordinator.state.goal)
            turn.tokens_after = self.coordinator.state.total_tokens
            self.coordinator.store.put("goal-turns", f"{turn.number:08d}", turn, immutable=False)

    async def _event(self, agent_id: str, event: dict[str, Any]) -> None:
        await super()._event(agent_id, event)
        turn = self._goal_turn
        if turn is None:
            return
        kind = event["type"]
        if kind == "model_output":
            turn.output.append(event.get("text", ""))
        elif kind in {"tool_started", "tool_completed"}:
            call_id = event.get("call_id") or event.get("name", "tool")
            if kind == "tool_started":
                self._running_tools[call_id] = event.get("name", "tool")
            else:
                self._running_tools.pop(call_id, None)
            turn.last_tool = next(reversed(self._running_tools.values()), None)
            turn.tool_running = bool(self._running_tools)
        elif kind == "message_injected":
            message = self.coordinator.store.get("messages", event["message_id"], ResearchMessage)
            if all(item.message_id != message.message_id for item in turn.inputs):
                turn.inputs.append(message)
        elif kind != "runtime_usage":
            return
        self._save_turn()

    async def _drive(self, agent_id: str) -> None:
        c = self.coordinator
        # A crashed worker cannot close its last round; preserve it as interrupted.
        path = c.store.root / "goal-turns" / f"{c.state.goal_turn_count:08d}.json"
        if path.exists():
            previous = c.store.get("goal-turns", path.stem, GoalTurn)
            if previous.status == "running":
                previous.status, previous.finished_at = "interrupted", now()
                previous.last_tool = None
                previous.tool_running = False
                c.store.put("goal-turns", path.stem, previous, immutable=False)
        self._goal_turn = None
        try:
            await super()._drive(agent_id)
        finally:
            if (turn := self._goal_turn) is not None:
                actor = c.actor(agent_id)
                turn.status = (
                    "failed"
                    if actor.state == AgentState.FAILED
                    else "completed"
                    if c.state.status in {"completed", "succeeded"}
                    else "interrupted"
                    if actor.wait_reason in {"run_paused", "worker_interrupted"}
                    or c.state.status in {"paused", "stopped"}
                    else "blocked"
                    if c.state.goal and c.state.goal.status == "blocked"
                    else "completed"
                )
                turn.error, turn.finished_at = actor.error, now()
                turn.last_tool = None
                self._running_tools.clear()
                turn.tool_running = False
                self._save_turn()
                self._goal_turn = None

    async def tick(self) -> int:
        c = self.coordinator
        self._acquire()
        goal, actor = c.state.goal, c.actor("rsi")
        if c.state.status == "running" and goal is not None:
            if actor.state == AgentState.FAILED and goal.status != "blocked":
                goal.status, goal.blocker = "blocked", actor.error or "Agent failed; retry required"
                goal.updated_at, goal.revision = now(), goal.revision + 1
                c.save()
                c.store.event(
                    "goal_updated", agent_id="rsi", goal=encode(goal), summary=goal.summary
                )
            if (
                goal.status == "active"
                and actor.state == AgentState.WAITING
                and "rsi" not in c.state.active
                and not c.state.inboxes.get("rsi")
                and not self._jobs_pending
            ):
                c.enqueue(
                    "system",
                    "rsi",
                    "继续未完成的目标，核对进度并执行下一步，更新 Goal 上下文。",
                    key="pass1-continue:"
                    + (c.state.delivered[-1] if c.state.delivered else "initial"),
                )
        return await super().tick()
