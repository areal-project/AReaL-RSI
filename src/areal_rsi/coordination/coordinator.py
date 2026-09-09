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
from areal_rsi.api.research import Assignment, ResearchMessage, ResearchReport, TaskBrief
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
            if (message.sender, message.recipient, message.text) != (sender, recipient, text):
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
                text=text if recipient == "user" or sender == "user" else None,
            )
        if recipient != "user":
            inbox = self.state["inboxes"].setdefault(recipient, [])
            if message_id not in self.state["delivered"] and message_id not in inbox:
                inbox.append(message_id)
        self.save()
        return message_id

    def start(self) -> None:
        if self.state["status"] != "planned":
            raise ValueError("Run has already started")
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
            # Preserve the active delivery for explicit recovery of the same turn.
            return
        identifier = self.state["active"].pop(agent_id, None)
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
        completion_criteria: str,
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
        completion_criteria: str,
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
                report = decode(ResearchReport, self.store.public_record(previous.report_ref))
                if reply_to_report_id != report.report_id:
                    raise ValueError("Continuation must reply to the latest report explicitly")
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
            json.dumps(encode(assignment), ensure_ascii=False),
            key=assignment_id,
            references=references,
            reply_to=reply_to_report_id,
        )
        return {"agent_id": agent_id, "iteration_id": node_id, "assignment_id": assignment_id}

    def report(self, agent_id: str, *, key: str, fields: dict[str, Any]) -> dict[str, str]:
        actor = self.actor(agent_id)
        if not actor.iterations:
            raise ValueError("No assigned research node")
        node = next(n for n in actor.iterations if n.iteration_id == actor.current_iteration_id)
        assignment = decode(Assignment, self.store.public_record(node.assignment_ref))
        report_id = effect_id("report", key)
        path = self.store.root / "reports" / f"{report_id}.json"
        if path.exists():
            report = self.store.get("reports", report_id, ResearchReport)
        else:
            report = ResearchReport(
                report_id, node.iteration_id, assignment.assignment_id, created_at=now(), **fields
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
            "研究报告：\n" + json.dumps(encode(report), ensure_ascii=False),
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

    def stop(self, reason: str) -> None:
        self.state["status"] = "stopped"
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
            },
            "waiting_for_user": self.state["waiting_for_user"],
            "budget_stop_reason": self.state.get("budget_stop_reason"),
        }
