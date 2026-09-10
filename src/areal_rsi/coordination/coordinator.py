"""Research state transitions. Model turns and background jobs live in other modules."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from areal_rsi.api.base import (
    AgentState,
    ExperimentPlan,
    ExperimentRun,
    IterationState,
    RecordRef,
    ResearchAgentState,
    ResearchIteration,
    RunState,
)
from areal_rsi.api.research import (
    Assignment,
    ResearchMessage,
    ResearchReport,
    TaskBrief,
    VerifiedResult,
)
from areal_rsi.base.storage import decode, encode, new_id, now, safe_name, write_json
from areal_rsi.coordination.store import ResearchStore


def effect_id(prefix: str, key: str) -> str:
    return prefix + "_" + hashlib.sha256(key.encode()).hexdigest()[:24]


class Coordinator:
    """Single-owner durable state machine; no model request occurs in this class."""

    def __init__(self, store: ResearchStore):
        self.store = store
        self.path = store.root / "coordination" / "state.json"
        self.state: dict[str, Any] = json.loads(self.path.read_text())

    @classmethod
    def create(
        cls,
        root: Path,
        brief: TaskBrief,
        *,
        run_id: str | None = None,
        simulated: bool = False,
    ) -> Coordinator:
        store = ResearchStore(root, run_id or new_id("research"))
        if store.paths.state.exists():
            raise FileExistsError(store.run_id)
        brief_ref = store.put("briefs", "initial", brief)
        store.runs.save(
            store.run_id,
            ExperimentRun(
                store.run_id,
                ExperimentPlan(
                    brief.objective,
                    "research",
                    "",
                    None,
                    None,
                    brief.limits.max_nodes,
                    brief.limits.max_agents,
                ),
                RunState.PLANNED,
                brief_ref=brief_ref,
            ),
        )
        for name in ("rsi", "interaction"):
            write_json(
                store.paths.agent(name) / "agent.json",
                ResearchAgentState(name, AgentState.WAITING, session_id=name, wait_reason="user"),
            )
        write_json(
            store.root / "coordination" / "state.json",
            {
                "status": "planned",
                "simulated": simulated,
                "inboxes": {},
                "active": {},
                "delivered": [],
                "plan": "",
                "seed_candidate_id": None,
                "baseline_candidate_id": None,
                "model_calls": 0,
                "total_tokens": 0,
                "verify_calls": 0,
                "started_at": None,
                "waiting_for_user": [],
            },
        )
        store.event("run_created", objective=brief.objective, simulated=simulated)
        return cls(store)

    def save(self) -> None:
        write_json(self.path, self.state)

    def actor(self, agent_id: str) -> ResearchAgentState:
        safe_name(agent_id)
        if agent_id in {"rsi", "interaction"}:
            actor: ResearchAgentState = decode(
                ResearchAgentState,
                json.loads((self.store.paths.agent(agent_id) / "agent.json").read_text()),
            )
            return actor
        for agent in self.store.run().research_agents:
            if agent.agent_id == agent_id:
                return agent
        raise KeyError(agent_id)

    def actors(self) -> list[ResearchAgentState]:
        return [self.actor("rsi"), self.actor("interaction"), *self.store.run().research_agents]

    def save_actor(self, actor: ResearchAgentState) -> None:
        if actor.agent_id in {"rsi", "interaction"}:
            write_json(self.store.paths.agent(actor.agent_id) / "agent.json", actor)
        else:
            run = self.store.run()
            run.research_agents = tuple(
                actor if item.agent_id == actor.agent_id else item for item in run.research_agents
            )
            self.store.save_run(run)

    def enqueue(
        self,
        sender: str,
        recipient: str,
        text: str,
        *,
        key: str,
        references: tuple[RecordRef, ...] = (),
        reply_to: str | None = None,
    ) -> str:
        message_id = effect_id("message", key)
        try:
            message = self.store.get("messages", message_id, ResearchMessage)
            if (message.sender, message.recipient, message.text, message.reply_to) != (
                sender,
                recipient,
                text,
                reply_to,
            ):
                raise ValueError("Message idempotency key reused for different content")
        except FileNotFoundError:
            message = ResearchMessage(
                message_id, sender, recipient, text, now(), references, reply_to
            )
            self.store.put("messages", message_id, message)
            self.store.event(
                "message",
                sender=sender,
                recipient=recipient,
                message_id=message_id,
                reply_to=reply_to,
                text=text if recipient == "user" or sender == "user" else None,
            )
        if recipient != "user":
            inbox = self.state["inboxes"].setdefault(recipient, [])
            if message_id not in self.state["delivered"] and message_id not in inbox:
                inbox.append(message_id)
        self.save()
        return message_id

    def user_questions(self) -> list[dict[str, Any]]:
        """Project outstanding questions, including records written before direct RSI replies."""
        pending = self.state["waiting_for_user"]
        if not pending:
            return []
        answered = {
            message.reply_to
            for message in self.store.records("messages", ResearchMessage)
            if message.sender == "user" and message.reply_to
        }
        questions = []
        for item in pending:
            message_id = item["message_id"]
            if message_id in answered:
                continue
            message = self.store.get("messages", message_id, ResearchMessage)
            questions.append(
                {
                    "message_id": message_id,
                    "agent_id": message.sender,
                    "question": message.text,
                    "created_at": message.created_at,
                }
            )
        return questions

    def ask_user(self, agent_id: str, question: str, *, key: str) -> str:
        if agent_id not in {"rsi", "interaction"}:
            raise ValueError("Only RSI and Interaction can ask the user")
        if self.state["status"] not in {"planned", "running", "paused"}:
            raise ValueError("研究已结束，不能再创建待回复问题")
        if not question.strip():
            raise ValueError("请说明需要用户回答的问题")
        message_id = self.enqueue(agent_id, "user", question, key=key)
        if not any(q["message_id"] == message_id for q in self.state["waiting_for_user"]):
            self.state["waiting_for_user"].append({"message_id": message_id})
        # Replaying an interrupted tool must not reopen an already answered question.
        self.state["waiting_for_user"] = self.user_questions()
        self.save()
        self.wait(agent_id, "user")
        return message_id

    def user_message(
        self,
        text: str,
        *,
        key: str,
        reply_to: str | None = None,
        recipient: str | None = None,
    ) -> str:
        """Deliver a user reply to its question's author, or send an independent message."""
        if self.state["status"] not in {"planned", "running", "paused"}:
            raise ValueError("研究已结束，记录仍可查看；新的研究请另建对话")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 12000:
            raise ValueError("Message must contain 1–12000 characters")
        text = text.strip()
        if recipient is not None and recipient not in {"rsi", "interaction"}:
            raise ValueError("用户消息只能发给 RSI 或 Interaction")
        if reply_to is not None and (not isinstance(reply_to, str) or not reply_to):
            raise ValueError("reply_to 必须是问题的 message_id")
        try:
            previous = self.store.get("messages", effect_id("message", key), ResearchMessage)
        except FileNotFoundError:
            previous = None
        if previous:
            if (
                previous.sender != "user"
                or previous.text != text
                or (reply_to is not None and previous.reply_to != reply_to)
                or (recipient is not None and previous.recipient != recipient)
            ):
                raise ValueError("Message idempotency key reused for different content")
            # Preserve routing on HTTP retries, even after the question was answered.
            recipient, reply_to = previous.recipient, previous.reply_to
        else:
            questions = self.user_questions()
            if reply_to is None and recipient is None and len(questions) == 1:
                reply_to = questions[0]["message_id"]
            if reply_to is not None:
                question = next((q for q in questions if q["message_id"] == reply_to), None)
                if question is None:
                    raise ValueError("该问题不存在或已回复，请刷新后再发送")
                if recipient is not None and recipient != question["agent_id"]:
                    raise ValueError("回复必须发送给原提问者")
                recipient = question["agent_id"]
        recipient = recipient or "interaction"
        if recipient == "rsi" and self.state["started_at"] is None:
            raise ValueError("研究尚未开始，请先与 Interaction 确认需求")
        message_id = self.enqueue("user", recipient, text, key=key, reply_to=reply_to)
        if previous is None and self.actor(recipient).state == AgentState.FAILED:
            # A new user instruction is an explicit request to continue, not a
            # background retry loop. Preserve the interrupted delivery and tools.
            self.retry_agent(recipient)
        # The archived reply is authoritative if a process stopped between these writes.
        self.state["waiting_for_user"] = self.user_questions()
        self.save()
        return message_id

    def start(self) -> None:
        if self.state["status"] != "planned":
            raise ValueError("Run has already started")
        if "intake" in self.state and self.state["intake"].get("status") != "ready":
            raise ValueError("请先与 Interaction 确认研究目标和执行条件")
        self.state["status"] = "running"
        self.state["started_at"] = now()
        run = self.store.run()
        run.state, run.started_at = RunState.RUNNING, self.state["started_at"]
        self.store.save_run(run)
        self.enqueue(
            "interaction",
            "rsi",
            "开始本次研究，以下是用户目标与约束：\n"
            + json.dumps(encode(self.store.brief()), ensure_ascii=False),
            key="initial",
        )
        self.store.event("run_started")

    def pending(self) -> list[str]:
        if self.state["status"] not in {"planned", "running"}:
            return []
        ready = []
        for actor in self.actors():
            if self.state["status"] == "planned" and actor.agent_id != "interaction":
                continue
            if actor.state in {AgentState.STOPPED, AgentState.PAUSED, AgentState.FAILED}:
                continue
            if self.state["inboxes"].get(actor.agent_id) or actor.agent_id in self.state["active"]:
                ready.append(actor.agent_id)
        return ready

    def begin_turn(self, agent_id: str) -> tuple[ResearchMessage, bool]:
        actor = self.actor(agent_id)
        if agent_id not in self.pending():
            raise ValueError("Agent has no authorized pending turn")
        active = self.state["active"].get(agent_id)
        resumed = active is not None
        identifier = active or self.state["inboxes"][agent_id][0]
        self.state["active"][agent_id] = identifier
        actor.state, actor.wait_reason = AgentState.RUNNING, None
        self.save_actor(actor)
        self.save()
        self.store.event("agent_running", agent_id=agent_id, resumed=resumed)
        return self.store.get("messages", identifier, ResearchMessage), resumed

    def end_turn(
        self, agent_id: str, *, error: str | None = None, missing_report: bool = False
    ) -> None:
        if error is not None:
            actor = self.actor(agent_id)
            failures = self.state.setdefault("turn_failures", {})
            if missing_report:
                if agent_id in {"rsi", "interaction"} or agent_id not in self.state["active"]:
                    raise ValueError("Missing-report recovery requires an active research delivery")
                failures[agent_id] = {
                    "kind": "missing_report",
                    "message_id": self.state["active"][agent_id],
                    "iteration_id": actor.current_iteration_id,
                }
            else:
                failures.pop(agent_id, None)
            self.save()
            actor.state, actor.error = AgentState.FAILED, error
            self.save_actor(actor)
            self.store.event("agent_failed", agent_id=agent_id, error=error)
            self.enqueue(
                "runtime",
                "user",
                f"{agent_id} 已停止：{error}。已有进度和对话已保存。"
                "你可以发送补充消息继续，或点击重试。",
                key="failure:" + effect_id(agent_id, error + str(self.state.get("model_calls"))),
            )
            if agent_id not in {"rsi", "interaction"}:
                self.enqueue(
                    "runtime",
                    "rsi",
                    f"研究员 {agent_id} 执行停止：{error}。原任务、会话与已完成操作均已保存。"
                    "请检查原因，决定用 retry_researcher 恢复、停止方向或向用户报告阻塞。",
                    key="researcher-failure:"
                    + effect_id(agent_id, error + str(self.state.get("model_calls"))),
                )
            # Preserve the active delivery for explicit recovery of the same turn.
            return
        identifier = self.state["active"].pop(agent_id, None)
        self.state.get("turn_followups", {}).pop(agent_id, None)
        self.state.get("turn_retries", {}).pop(agent_id, None)
        self.state.get("turn_failures", {}).pop(agent_id, None)
        if identifier:
            if identifier not in self.state["delivered"]:
                self.state["delivered"].append(identifier)
            inbox = self.state["inboxes"].get(agent_id, [])
            self.state["inboxes"][agent_id] = [key for key in inbox if key != identifier]
        actor = self.actor(agent_id)
        if actor.state == AgentState.RUNNING:
            actor.state, actor.wait_reason = AgentState.WAITING, "message"
        self.save_actor(actor)
        self.save()

    def receive_during_turn(self, agent_id: str, message_id: str) -> None:
        """Acknowledge user or supervisor updates after the runtime has saved them."""
        message = self.store.get("messages", message_id, ResearchMessage)
        allowed = {"user"} if agent_id in {"rsi", "interaction"} else {"rsi"}
        if message.sender not in allowed or message.recipient != agent_id:
            raise ValueError("Only the agent's user or supervisor may update an active delivery")
        followups = self.state.setdefault("turn_followups", {}).setdefault(agent_id, [])
        if message_id not in followups:
            followups.append(message_id)
        if message_id not in self.state["delivered"]:
            self.state["delivered"].append(message_id)
        self.state["inboxes"][agent_id] = [
            item for item in self.state["inboxes"].get(agent_id, []) if item != message_id
        ]
        self.save()

    def current_message_ids(self, agent_id: str) -> list[str]:
        active = self.state["active"].get(agent_id)
        result: list[str] = ([active] if active else []) + self.state.get("turn_followups", {}).get(
            agent_id, []
        )
        return result

    def retry_agent(self, agent_id: str) -> None:
        actor = self.actor(agent_id)
        if actor.state != AgentState.FAILED:
            raise ValueError("Only a failed agent can be retried")
        active = self.state["active"].get(agent_id)
        failure = self.state.get("turn_failures", {}).get(agent_id, {})
        if failure.get("kind") == "missing_report":
            retry_id = self._retry_missing_report(actor, failure)
        elif active is not None:
            retry_id = new_id("retry")
            self.state.setdefault("turn_retries", {})[agent_id] = {
                "message_id": active,
                "retry_id": retry_id,
            }
            self.save()
        else:
            retry_id = new_id("retry")
        actor.state, actor.error = AgentState.WAITING, None
        self.save_actor(actor)
        self.store.event("agent_retry_requested", agent_id=agent_id, retry_id=retry_id)
        self.enqueue(
            "runtime",
            "user",
            f"已恢复 {agent_id}，将从保存的对话和进度继续。",
            key="retry-notice:" + retry_id,
        )

    def _retry_missing_report(self, actor: ResearchAgentState, failure: dict[str, Any]) -> str:
        """Correct a completed model reply without creating another research node."""
        agent_id = actor.agent_id
        original = failure["message_id"]
        node = next(
            (item for item in actor.iterations if item.iteration_id == actor.current_iteration_id),
            None,
        )
        if (
            node is None
            or node.iteration_id != failure["iteration_id"]
            or node.report_ref is not None
            or self.state["active"].get(agent_id) not in {None, original}
        ):
            raise ValueError("Missing-report recovery no longer matches the active research node")
        retries = self.state.setdefault("turn_retries", {})
        intent = retries.get(agent_id, {})
        if intent.get("mode") != "report" or intent.get("message_id") != original:
            intent = {
                "mode": "report",
                "message_id": original,
                "retry_id": new_id("retry"),
            }
            retries[agent_id] = intent
            self.save()
        # Persist intent first, then acknowledge the completed delivery. If the
        # process dies before enqueue, a second explicit retry uses the same ID.
        self.state["active"].pop(agent_id, None)
        if original not in self.state["delivered"]:
            self.state["delivered"].append(original)
        inbox = self.state["inboxes"].get(agent_id, [])
        self.state["inboxes"][agent_id] = [key for key in inbox if key != original]
        self.save()
        self.enqueue(
            "rsi",
            agent_id,
            "用户已请求恢复现有研究节点的报告协议："
            + node.iteration_id
            + "。你上次的最终回复没有提交要求的报告。"
            "在同一个节点和会话中继续原任务，使用已有工作区、工具结果与证据，不要重复已完成的工具操作。"
            "调用 submit_report，写清已完成的工作、问题、实际结果、不确定性和剩余事项。"
            "如果外部依赖阻碍完成，使用明确的等待工具。"
            "本次修正不授权新任务、新节点或新方向；上报后等待 RSI 的明确回复。",
            key="report-retry:" + intent["retry_id"],
            reply_to=original,
        )
        return str(intent["retry_id"])

    def spawn(
        self,
        *,
        key: str,
        direction: str,
        instruction: str,
        completion_criteria: str = "",
        references: tuple[RecordRef, ...] = (),
    ) -> dict[str, str]:
        if self.state["status"] != "running":
            raise ValueError("Run is not running")
        agent_id = effect_id("research", key)
        run = self.store.run()
        if not any(a.agent_id == agent_id for a in run.research_agents):
            active = sum(a.state != AgentState.STOPPED for a in run.research_agents)
            if active >= self.store.brief().limits.max_agents:
                raise ValueError("Research agent limit reached; stop a direction before adding one")
            if not direction.strip():
                raise ValueError("Research direction must be concrete")
            run.research_agents += (
                ResearchAgentState(
                    agent_id,
                    direction=direction,
                    session_id=agent_id,
                ),
            )
            self.store.save_run(run)
            (self.store.paths.agent(agent_id) / "workspace").mkdir(parents=True, exist_ok=True)
            self.store.event("agent_created", agent_id=agent_id, direction=direction)
        return self.assign(
            agent_id,
            key=key,
            instruction=instruction,
            completion_criteria=completion_criteria,
            references=references,
        )

    def assign(
        self,
        agent_id: str,
        *,
        key: str,
        instruction: str,
        completion_criteria: str = "",
        reply_to_report_id: str | None = None,
        references: tuple[RecordRef, ...] = (),
    ) -> dict[str, str]:
        if self.state["status"] != "running":
            raise ValueError("Run is not running")
        actor = self.actor(agent_id)
        if agent_id in {"rsi", "interaction"}:
            raise ValueError("Research assignments target research agents")
        assignment_id, node_id = effect_id("assignment", key), effect_id("node", key)
        existing = next((n for n in actor.iterations if n.iteration_id == node_id), None)
        if existing is None:
            if actor.state not in {AgentState.CREATED, AgentState.WAITING}:
                raise ValueError("Researcher must finish its current assignment before a new one")
            if actor.iterations:
                previous = actor.iterations[-1]
                if previous.report_ref is None:
                    raise ValueError("Researcher has not submitted its report")
                report = ResearchReport.from_record(self.store.public_record(previous.report_ref))
                if reply_to_report_id is not None and reply_to_report_id != report.report_id:
                    raise ValueError("Continuation must reply to the latest report explicitly")
                reply_to_report_id = report.report_id
            if sum(len(a.iterations) for a in self.store.run().research_agents) >= (
                self.store.brief().limits.max_nodes
            ):
                raise ValueError("Research node budget exhausted")
            path = self.store.root / "assignments" / f"{assignment_id}.json"
            if path.exists():
                assignment = self.store.get("assignments", assignment_id, Assignment)
            else:
                assignment = Assignment(
                    assignment_id,
                    agent_id,
                    instruction,
                    completion_criteria,
                    now(),
                    references,
                    reply_to_report_id,
                )
                self.store.put("assignments", assignment_id, assignment)
            node = ResearchIteration(
                node_id,
                self.store.run_id,
                agent_id,
                self.store.paths.reference(path),
                IterationState.RUNNING,
                actor.iterations[-1].iteration_id if actor.iterations else None,
                references=references,
                created_at=assignment.created_at,
                updated_at=assignment.created_at,
            )
            actor.iterations += (node,)
            actor.current_iteration_id = node_id
            actor.state, actor.wait_reason = AgentState.WAITING, "assignment"
            self.save_actor(actor)
            self.store.event(
                "assignment_created",
                agent_id=agent_id,
                iteration_id=node_id,
                instruction=instruction,
                completion_criteria=completion_criteria,
                parent_iteration_id=node.parent_iteration_id,
            )
        else:
            assignment = self.store.get("assignments", assignment_id, Assignment)
        self.enqueue(
            "rsi",
            agent_id,
            assignment.instruction
            + (
                "\n\n完成条件：" + assignment.completion_criteria
                if assignment.completion_criteria
                else ""
            )
            + ("\n\n相关记录：\n" + "\n".join(ref.uri for ref in references) if references else ""),
            key=assignment_id,
            references=references,
            reply_to=reply_to_report_id,
        )
        return {"agent_id": agent_id, "iteration_id": node_id, "assignment_id": assignment_id}

    def pending_updates(self, agent_id: str) -> list[str]:
        actor = self.actor(agent_id)
        node = next(n for n in actor.iterations if n.iteration_id == actor.current_iteration_id)
        assignment = self.store.public_record(node.assignment_ref)
        original = effect_id("message", assignment["assignment_id"])
        return [
            identifier
            for identifier in self.state["inboxes"].get(agent_id, [])
            if identifier != self.state["active"].get(agent_id)
            and self.store.get("messages", identifier, ResearchMessage).reply_to == original
        ]

    def message_researcher(self, agent_id: str, instruction: str, *, key: str) -> str:
        if agent_id in {"rsi", "interaction"} or self.state["status"] != "running":
            raise ValueError("任务补充只能发给运行中的研究方向")
        if not instruction.strip():
            raise ValueError("任务补充不能为空")
        identifier = effect_id("message", key)
        path = self.store.root / "messages" / (identifier + ".json")
        if path.exists():
            previous = self.store.get("messages", identifier, ResearchMessage)
            if previous.recipient != agent_id or previous.text != instruction:
                raise ValueError("Task update key reused with different content")
            return identifier
        actor = self.actor(agent_id)
        node = next(
            (n for n in actor.iterations if n.iteration_id == actor.current_iteration_id), None
        )
        if (
            node is None
            or node.report_ref is not None
            or actor.state
            not in {
                AgentState.RUNNING,
                AgentState.WAITING,
            }
        ):
            raise ValueError("当前没有未完成的任务；收到报告后请用 assign 下发下一项任务")
        assignment = self.store.public_record(node.assignment_ref)
        return self.enqueue(
            "rsi",
            agent_id,
            instruction,
            key=key,
            reply_to=effect_id("message", assignment["assignment_id"]),
        )

    def link_reference(
        self, agent_id: str, ref: RecordRef, *, iteration_id: str | None = None
    ) -> None:
        """Attach a tool-produced record to its task, without asking the model to copy IDs."""
        actor = self.actor(agent_id)
        identifier = iteration_id or actor.current_iteration_id
        node = next((n for n in actor.iterations if n.iteration_id == identifier), None)
        if node is not None and ref not in node.references:
            node.references = (*node.references, ref)
            self.save_actor(actor)

    def report(self, agent_id: str, *, key: str, fields: dict[str, Any]) -> dict[str, str]:
        actor = self.actor(agent_id)
        if not actor.iterations:
            raise ValueError("No assigned research node")
        node = next(n for n in actor.iterations if n.iteration_id == actor.current_iteration_id)
        if self.pending_updates(agent_id):
            raise ValueError("RSI 有新的任务补充尚未处理，请先接收补充消息再提交报告")
        assignment = decode(Assignment, self.store.public_record(node.assignment_ref))
        report_id = effect_id("report", key)
        path = self.store.root / "reports" / f"{report_id}.json"
        if path.exists():
            report = self.store.get("reports", report_id, ResearchReport)
        else:
            report = ResearchReport(
                report_id,
                node.iteration_id,
                assignment.assignment_id,
                ResearchReport.message_from(fields),
                now(),
                tuple(
                    RecordRef(uri)
                    for uri in dict.fromkeys(
                        ref.uri for ref in (*node.references, *fields.get("references", ()))
                    )
                ),
            )
            self.store.put("reports", report_id, report)
        if actor.state == AgentState.STOPPED or node.state == IterationState.CANCELLED:
            self.store.event("late_report_archived", agent_id=agent_id, report_id=report_id)
            return {"report_id": report_id, "status": "archived_after_stop"}
        if node.report_ref is not None and node.report_ref != self.store.paths.reference(path):
            raise ValueError("Assignment already has a report")
        node.report_ref = self.store.paths.reference(path)
        node.references = tuple(
            RecordRef(uri)
            for uri in dict.fromkeys(ref.uri for ref in (*node.references, *report.references))
        )
        node.state, node.updated_at = IterationState.COMPLETED, report.created_at
        actor.state, actor.wait_reason = AgentState.WAITING, "rsi_reply"
        self.save_actor(actor)
        self.enqueue(
            agent_id,
            "rsi",
            "研究员回复：\n"
            + (
                report.message
                if len(report.message) <= 4000
                else report.message[:3200]
                + "\n[长报告的中间内容已省略，完整内容见记录]\n"
                + report.message[-800:]
            )
            + "\n\n报告记录："
            + node.report_ref.uri,
            key=report_id,
            references=(node.report_ref,),
            reply_to=assignment.assignment_id,
        )
        self.store.event(
            "report_submitted",
            agent_id=agent_id,
            iteration_id=node.iteration_id,
            report_id=report_id,
        )
        return {"report_id": report_id, "status": "waiting_for_rsi"}

    def wait(self, agent_id: str, reason: str) -> None:
        if not reason.strip():
            raise ValueError("Waiting needs a concrete reason")
        actor = self.actor(agent_id)
        actor.state, actor.wait_reason = AgentState.WAITING, reason
        self.save_actor(actor)
        self.store.event("agent_waiting", agent_id=agent_id, reason=reason)

    def stop_agent(self, agent_id: str, reason: str) -> None:
        if agent_id in {"rsi", "interaction"}:
            raise ValueError("Use run controls to stop a controller agent")
        actor = self.actor(agent_id)
        actor.state, actor.wait_reason = AgentState.STOPPED, reason
        for node in actor.iterations:
            if node.state in {IterationState.PENDING, IterationState.RUNNING}:
                node.state, node.updated_at = IterationState.CANCELLED, now()
        self.save_actor(actor)
        self.state["inboxes"][agent_id] = []
        self.save()
        self.store.event("agent_stopped", agent_id=agent_id, reason=reason)

    def pause(self) -> None:
        if self.state["status"] != "running":
            raise ValueError("Only a running run can be paused")
        self.state["status"] = "paused"
        self.save()
        self.store.event("run_paused")

    def resume(self) -> None:
        if self.state["status"] != "paused":
            raise ValueError("Only a paused run can be resumed")
        self.state["status"] = "running"
        self.save()
        self.store.event("run_resumed")

    def reopen(self) -> None:
        """Explicit user recovery; completed work and cancelled executions stay archived."""
        if self.state["status"] != "stopped":
            raise ValueError("Only a stopped research run can be reopened")
        if not self.state.get("started_at"):
            raise ValueError("This discussion has not started research")
        stop_events = [event for event in self.store.events() if event.kind == "run_stopped"]
        stop_reason = stop_events[-1].data.get("reason") if stop_events else None
        stopped_by_run = self.state.get("run_stop_agents")
        for actor in self.actors():
            controller = actor.agent_id in {"rsi", "interaction"}
            reported = bool(actor.iterations and actor.iterations[-1].report_ref)
            was_active = (
                actor.agent_id in stopped_by_run
                if stopped_by_run is not None
                else actor.wait_reason == stop_reason
            )
            if controller or (reported and was_active):
                actor.state = AgentState.WAITING
                actor.wait_reason = "message" if controller else "rsi_reply"
                self.save_actor(actor)
                self.end_turn(actor.agent_id)
        run = self.store.run()
        run.state, run.finished_at = RunState.RUNNING, None
        self.store.save_run(run)
        self.state["status"] = "running"
        self.save()
        self.enqueue(
            "user",
            "rsi",
            "恢复本次研究。沿用已有会话、任务、候选和正式结果；先检查最后的阻塞与最新执行说明，"
            "再安排后续工作。已完成的评测不要重复执行；环境问题请向用户提问并等待，不能据此结束研究。",
            key=new_id("reopen"),
        )
        self.store.event("run_reopened")

    def execution_blocker(self) -> dict[str, Any] | None:
        results = self.store.records("verifications", VerifiedResult)
        latest = max(results, key=lambda result: result.created_at, default=None)
        if latest is None or latest.status != "infra_error":
            return None
        return {"job_id": latest.result_id, "error": latest.error, "created_at": latest.created_at}

    def stop(self, reason: str) -> None:
        if self.state["status"] != "stopped":
            self.state["run_stop_agents"] = [
                actor.agent_id for actor in self.actors() if actor.state != AgentState.STOPPED
            ]
        self.state["status"] = "stopped"
        self.state["waiting_for_user"] = []
        for actor in self.actors():
            if actor.agent_id not in {"rsi", "interaction"}:
                self.stop_agent(actor.agent_id, reason)
            else:
                actor.state = AgentState.STOPPED
                self.save_actor(actor)
        run = self.store.run()
        run.state, run.finished_at = RunState.CANCELLED, now()
        self.store.save_run(run)
        self.save()
        self.store.event("run_stopped", reason=reason)

    def summary(self) -> dict[str, Any]:
        run = self.store.run()
        started = self.state.get("started_at")
        elapsed = (
            max(
                0.0,
                (
                    datetime.fromisoformat(run.finished_at or now())
                    - datetime.fromisoformat(started)
                ).total_seconds(),
            )
            if started
            else 0.0
        )
        return {
            "run_id": run.run_id,
            "status": self.state["status"],
            "brief": encode(self.store.brief()),
            "plan": self.state["plan"],
            "execution_blocker": self.execution_blocker(),
            "agents": encode(self.actors()),
            "simulated": self.state["simulated"],
            "best": self.store.public_record(run.best_result_ref) if run.best_result_ref else None,
            "baseline": self.store.public_record(run.baseline_result_ref)
            if run.baseline_result_ref
            else None,
            "budget": {
                **{
                    name: self.state[name]
                    for name in ("model_calls", "total_tokens", "verify_calls")
                },
                "reserved_tokens": sum(
                    record.get("reserved_tokens", 0)
                    for record in self.state.get("model_requests", {}).values()
                ),
                "elapsed_seconds": elapsed,
                "api_attempts": sum(
                    record.get("api_attempts", 1)
                    for record in self.state.get("model_requests", {}).values()
                ),
            },
            "waiting_for_user": self.user_questions(),
            "budget_stop_reason": self.state.get("budget_stop_reason"),
            "intake": self.state.get("intake"),
        }
