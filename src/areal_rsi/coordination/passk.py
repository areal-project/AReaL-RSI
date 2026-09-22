"""Independent complete attempts, using the existing stores, jobs and scheduler."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from areal_rsi.agents.communication import INTEGRITY_PROMPT
from areal_rsi.api.base import AgentState, RecordRef, RunState
from areal_rsi.api.research import CandidateSnapshot, ResearchReport, VerifiedResult
from areal_rsi.base.storage import encode, now
from areal_rsi.coordination.actions import REFERENCES, STRING, ResearchActions, definition
from areal_rsi.coordination.coordinator import Coordinator, effect_id
from areal_rsi.coordination.engine import ResearchEngine
from areal_rsi.modules.agent_runtime import ToolCall, ToolHandler, ToolResult

PROMPT = """你是 Research Agent，独立负责完成整个研究任务。

先理解任务、起始代码和评测方法。开始时主动联网调研相关方法和资料，后续按需要继续调研。
自行提出方案、实现、验证并持续改进。你可以自由更换方向；每次都以实际验证结果为依据。
保存有价值的版本，使用正式验证工具检查正确性和目标指标。

可以向 RSI Agent 简要报告进展、结果和困难，但不等待它提供研究建议。
提交一个版本或完成一次报告不代表任务结束；研究方向由你自行决定。
你的工作区、会话和实验反馈只属于你。不要访问其他尝试的代码、记录或结果。
在系统通知停止前持续研究；暂时遇到困难或给出一次总结，不代表任务结束。

{{tool_instructions}}

""" + INTEGRITY_PROMPT.replace("如实报告并撤回", "如实记录并撤回")

RSI_PROMPT = """你是 RSI Agent，负责启动并监督 {{max_agents}} 个 Research Agent，
让他们各自独立、持续地完成整个研究任务。

将同一份完整任务和起始材料交给每个研究员，不拆分任务，不指定研究方向。

关注他们的进展和报告。只要尚未达到任务目标，且仍有时间和资源，
就提醒已经停下来的研究员继续独立探索。不要打断正在进行的研究。

你只负责督促继续，不提供技术建议、不调整研究方向，
不在研究员之间传递信息或分享产物，也不提前淘汰任何尝试。

是否达标以正式验证结果为准。遵守系统的停止条件，
不修改任务目标、评测规则或预算。

{{tool_instructions}}

不获取或提供题目的现成答案，不作弊，不伪造或隐瞒结果。
"""

SUPERVISOR_TOOLS = [
    {
        "type": "function",
        "name": "start_researchers",
        "description": "一次启动配置中固定数量的独立研究员，每人获得相同的完整任务。"
        "count 可省略；提供时必须等于当前配置。",
        "parameters": {
            "type": "object",
            "properties": {"count": {"type": "integer", "minimum": 1}},
            "required": [],
            "additionalProperties": False,
        },
        "strict": True,
    },
    definition("get_state", "查看研究员的进展、报告、正式成绩与剩余时间。"),
    definition(
        "continue_researcher",
        "提醒已停下来的研究员在原会话中继续；不附加研究建议。",
        agent_id=STRING,
    ),
    definition("wait", "结束本回合，等待研究员报告或系统提醒。", reason=STRING),
]
FINISH_RESEARCH = definition(
    "finish_research",
    "研究时间已到。读取 get_state 中每位研究员的最后报告，汇总实际完成的实验、"
    "最佳成绩与对应方案、失败或未完成项及限制，然后结束研究。脚本报告的成绩应明确"
    "标为研究员读取的实验结果，不能声称经过平台可信验证。",
    summary=STRING,
)
PROGRESS_TOOL = definition(
    "submit_report",
    "向 RSI 简要报告进展、结果或困难，报告后继续独立研究，不等待回复。",
    message=STRING,
    references=REFERENCES,
)
CONTINUE_MESSAGE = "研究尚未结束，还有时间和资源。请继续独立探索并验证你的方案。"
CLOSEOUT_MAX_RETRIES = 3
CLOSEOUT_FALLBACK_SECONDS = 10 * 60

ALLOWED_TOOLS = {
    "get_state",
    "read_record",
    "list_records",
    "read_candidate_file",
    "import_candidate",
    "list_files",
    "read_file",
    "write_file",
    "save_candidate",
    "checkout_candidate",
    "withdraw_candidate",
    "get_commands",
    "run_job",
    "rerun_job",
    "job_status",
    "read_job_log",
    "collect_job_output",
    "read_job_output_file",
    "wait_for_job",
    "verify_candidate",
    "search_web",
    "fetch_url",
}


class PassKActions(ResearchActions):
    def definitions(self, agent_id: str, *, include_hidden: bool = False) -> list[dict[str, Any]]:
        if self.c.state["status"] == "planned" and agent_id == "interaction":
            return super().definitions(agent_id, include_hidden=include_hidden)
        if agent_id == "rsi":
            return [
                *SUPERVISOR_TOOLS,
                *([FINISH_RESEARCH] if self.c.state.get("passk_closeout") else []),
            ]
        if agent_id == "interaction":
            return []
        return [
            tool
            for tool in super().definitions(agent_id, include_hidden=include_hidden)
            if tool["name"] in ALLOWED_TOOLS
        ] + [PROGRESS_TOOL]

    def candidate(
        self, agent_id: str, candidate_id: str, *, seed: bool = True
    ) -> CandidateSnapshot:
        candidate = self.c.store.get("candidates", candidate_id, CandidateSnapshot)
        if candidate.source_agent_id != agent_id and not (
            seed and candidate_id == self.c.state["seed_candidate_id"]
        ):
            raise ValueError("This candidate is not part of your attempt")
        return candidate

    def read_record(self, agent_id: str, uri: str) -> Any:
        if agent_id == "interaction" and self.c.state["status"] == "planned":
            return super().read_record(agent_id, uri)
        ref = self.c.store.record_reference(RecordRef(uri))
        value = self.c.store.public_record(ref)
        category, identifier = urlsplit(ref.uri).path.strip("/").split("/")
        if category == "briefs":
            return value
        if category == "candidates":
            self.candidate(agent_id, value["candidate_id"])
            return value
        if category == "verifications":
            self.candidate(agent_id, value["candidate_id"], seed=False)
            return value
        if category == "materials" and identifier.startswith("execution_guide_"):
            return value
        if value.get("agent_id") == agent_id:
            return value
        # Public/shared does not grant cross-attempt access in this mode.
        raise ValueError("This record is not part of your attempt")

    def handler(self, agent_id: str) -> ToolHandler:
        base = super().handler(agent_id)

        async def handle(call: ToolCall) -> ToolResult:
            try:
                if agent_id not in {"rsi", "interaction"}:
                    if candidate_id := call.arguments.get("candidate_id"):
                        self.candidate(agent_id, candidate_id, seed=call.name != "verify_candidate")
                    if job_id := call.arguments.get("job_id"):
                        jobs = json.loads(
                            (self.c.store.root / "coordination/jobs.json").read_text()
                        )
                        if jobs.get(job_id, {}).get("agent_id") != agent_id:
                            raise ValueError("This job is not part of your attempt")
                    if call.name == "verify_candidate" and call.arguments.get("phase") == "final":
                        raise ValueError(
                            "Use search for exploration; "
                            "the system performs final verification after stopping"
                        )
                return await base(call)
            except (ValueError, FileNotFoundError) as exc:
                return ToolResult({"error": str(exc)})

        return handle

    def state(self, agent_id: str) -> dict[str, Any]:
        c = self.c
        if agent_id == "rsi":
            jobs = _job_evidence(c)
            return {
                "run_id": c.store.run_id,
                "agent_id": "rsi",
                "status": c.state["status"],
                "brief": encode(c.store.brief()),
                "started_researchers": bool(c.state.get("passk_ready")),
                "budget": c.budget(),
                "closeout": c.state.get("passk_closeout"),
                "researchers": [
                    {
                        "agent_id": actor.agent_id,
                        "state": actor.state,
                        "wait_reason": actor.wait_reason,
                        "needs_continue": needs_continue(c, actor.agent_id),
                        "best": self.state(actor.agent_id)["best"],
                        "latest_report": c.state.get("passk_reports", {}).get(actor.agent_id),
                        "experiments": jobs.get(actor.agent_id, {"counts": {}, "recent": []}),
                    }
                    for actor in c.store.run().research_agents
                ],
            }
        actor = c.actor(agent_id)
        budget = c.budget()
        results = [
            result
            for result in c.store.records("verifications", VerifiedResult)
            if c.store.get("candidates", result.candidate_id, CandidateSnapshot).source_agent_id
            == agent_id
            and result.status == "succeeded"
            and result.passed
            and result.phase == "search"
            and result.contract_digest == c.store.contract_digest()
            and result.simulated == c.state["simulated"]
            and result.score is not None
            and not c.store.is_withdrawn(result.candidate_id)
        ]
        contract = c.store.brief().verification
        results.sort(
            key=lambda result: float(result.score or 0),
            reverse=bool(contract and contract.direction == "maximize"),
        )
        return {
            "run_id": c.store.run_id,
            "agent_id": agent_id,
            "status": c.state["status"],
            "brief": encode(c.store.brief()),
            "simulated": c.state["simulated"],
            "current_node": encode(actor.iterations[-1]) if actor.iterations else None,
            "seed_candidate_id": c.state["seed_candidate_id"],
            "workspace_candidate_id": self.workspace_candidate(agent_id),
            "best": encode(results[0]) if results else None,
            "budget": {
                name: budget[name]
                for name in (
                    "observed_at",
                    "elapsed_seconds",
                    "remaining_wall_seconds",
                    "deadline_at",
                )
            },
        }

    async def _apply(self, agent_id: str, key: str, name: str, args: dict[str, Any]) -> ToolResult:
        if name == "start_researchers":
            expected = self.c.store.brief().limits.max_agents
            if args.get("count", expected) != expected:
                raise ValueError(f"This pass@k run requires exactly {expected} researchers")
            await launch(self.c, self)
            return ToolResult(self.state("rsi"))
        if name == "continue_researcher":
            if self.c.state.get("passk_closeout"):
                raise ValueError("研究时间已到，当前只能汇总已有结果")
            target = args["agent_id"]
            if target not in {actor.agent_id for actor in self.c.store.run().research_agents}:
                raise ValueError("Unknown researcher")
            if not needs_continue(self.c, target):
                return ToolResult({"continued": False, "reason": "研究员正在执行或等待自己的作业"})
            message_id = self.c.enqueue("rsi", target, CONTINUE_MESSAGE, key=key)
            return ToolResult({"continued": True, "message_id": message_id})
        if name == "finish_research":
            if agent_id != "rsi" or not self.c.state.get("passk_closeout"):
                raise ValueError("只有到时后的 RSI 收尾回合可以结束 pass@k 研究")
            summary = args["summary"].strip()
            if not summary:
                raise ValueError("最终总结不能为空")
            _complete_closeout(self.c, summary, key=key)
            return ToolResult({"completed": True}, stop=True, reason="completed")
        if name == "submit_report":
            return ToolResult(self.progress(agent_id, key, args["message"], args["references"]))
        if name == "get_state" and agent_id != "interaction":
            return ToolResult(self.state(agent_id))
        if name == "withdraw_candidate":
            self.candidate(agent_id, args["candidate_id"], seed=False)
            ref = self.c.store.withdraw_candidate(
                args["candidate_id"],
                args["reason"],
                self.refs(agent_id, args["references"]),
                agent_id=agent_id,
                simulated_run=self.c.state["simulated"],
                scope_agent_id=agent_id,
            )
            return ToolResult(
                {"withdrawn": True, "uri": ref.uri, "best": self.state(agent_id)["best"]}
            )
        return await super()._apply(agent_id, key, name, args)

    def progress(
        self, agent_id: str, key: str, message: str, references: list[str]
    ) -> dict[str, Any]:
        c = self.c
        actor = c.actor(agent_id)
        node = next(n for n in actor.iterations if n.iteration_id == actor.current_iteration_id)
        report_id = effect_id("report", key)
        path = c.store.root / "reports" / (report_id + ".json")
        if path.exists():
            report = ResearchReport.from_record(json.loads(path.read_text()))
        else:
            report = ResearchReport(
                report_id,
                node.iteration_id,
                c.store.public_record(node.assignment_ref)["assignment_id"],
                message,
                now(),
                self.refs(agent_id, references),
            )
            c.store.put("reports", report_id, {**encode(report), "agent_id": agent_id})
        node.report_ref = c.store.paths.reference(path)
        c.save_actor(actor)  # A progress report does not complete its research node.
        c.state.setdefault("passk_reports", {})[agent_id] = {
            "uri": node.report_ref.uri,
            "message": report.message[:4000],
            "created_at": report.created_at,
        }
        c.save()
        c.enqueue(
            agent_id,
            "rsi",
            "研究员进展：\n" + report.message[:4000],
            key=report_id,
            references=(node.report_ref,),
            event_kind="report_submitted",
            event_data={
                "agent_id": agent_id,
                "iteration_id": node.iteration_id,
                "report_id": report_id,
            },
        )
        return {"report_id": report_id, "status": "recorded", "continue_research": True}


def needs_continue(c: Coordinator, agent_id: str) -> bool:
    actor = c.actor(agent_id)
    return (
        c.state["status"] == "running"
        and actor.state == AgentState.WAITING
        and actor.wait_reason == "passk_supervision"
        and agent_id not in c.state["active"]
        and not c.state["inboxes"].get(agent_id)
    )


async def launch(c: Coordinator, actions: PassKActions) -> None:
    """RSI invokes this fixed launch; no sampled assignments or replacement attempts."""
    if c.state.get("passk_ready"):
        return
    for agent_id in ("interaction",):
        actor = c.actor(agent_id)
        actor.state, actor.wait_reason = AgentState.STOPPED, "passk: no intervention"
        c.save_actor(actor)
        c.state["inboxes"][agent_id] = []
        c.state["active"].pop(agent_id, None)
    c.save()
    brief = c.store.brief()
    instruction = (
        "请独立完成下面的完整任务，自行调研、实现、实验和改进，直到系统通知停止。\n\n"
        + brief.objective
        + "\n\n"
        + brief.instructions
    )
    for index in range(brief.limits.max_agents):
        result = c.spawn(
            key=f"passk-{index + 1}", direction=f"独立探索 {index + 1}", instruction=instruction
        )
        await actions.initialize_workspace(result["agent_id"])
    c.state["passk_ready"] = True
    c.save()
    c.store.event("passk_launched", k=brief.limits.max_agents)


def stop_on_target(c: Coordinator) -> None:
    contract = c.store.brief().verification
    best = c.store.run().best_result_ref
    if c.state["status"] != "running" or not contract or contract.target is None or not best:
        return
    result = c.store.public_record(best)
    score = result["score"]
    reached = (
        score >= contract.target if contract.direction == "maximize" else score <= contract.target
    )
    if reached:
        c.state["passk_target"] = {"result": result, "observed_at": now()}
        c.save()
        c.stop("已有独立尝试通过正式验证并达到目标，停止整组探索")
        c.state["status"] = "succeeded"
        c.save()
        run = c.store.run()
        run.state = RunState.SUCCEEDED
        c.store.save_run(run)


def _job_evidence(c: Coordinator) -> dict[str, dict[str, Any]]:
    """Return compact, persisted experiment evidence for closeout."""
    path = c.store.root / "coordination" / "jobs.json"
    if not path.exists():
        return {}
    try:
        jobs = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for job_id, info in jobs.items():
        if not isinstance(info, dict) or not isinstance(info.get("agent_id"), str):
            continue
        agent_id = info["agent_id"]
        state = str((info.get("result") or {}).get("state") or "running")
        entry = result.setdefault(agent_id, {"counts": {}, "recent": []})
        entry["counts"][state] = entry["counts"].get(state, 0) + 1
        metrics: dict[str, Any] = {}
        feedback = info.get("feedback") or {}
        try:
            payload = json.loads(feedback.get("content", "{}"))
            raw = payload.get("status", payload).get("metrics", {})
            metrics = {
                key: value
                for key, value in raw.items()
                if isinstance(value, (int, float, bool)) and not isinstance(value, str)
            }
        except (AttributeError, TypeError, ValueError):
            pass
        entry["recent"].append(
            {
                "job_id": job_id,
                "state": state,
                "candidate_id": info.get("candidate_id"),
                "created_at": info.get("created_at"),
                "metrics": metrics,
            }
        )
    for entry in result.values():
        entry["recent"] = sorted(
            entry["recent"], key=lambda item: str(item.get("created_at") or "")
        )[-5:]
    return result


def _fallback_closeout_summary(c: Coordinator) -> str:
    evidence = _job_evidence(c)
    lines = [
        "研究时间已到。RSI 模型收尾连续失败，平台已根据持久化实验记录生成兜底汇总。",
        "以下成绩来自现有实验脚本反馈，不是平台可信正式验证结果。",
    ]
    reports = c.state.get("passk_reports", {})
    for actor in c.store.run().research_agents:
        item = evidence.get(actor.agent_id, {"counts": {}, "recent": []})
        counts = item["counts"]
        count_text = "、".join(f"{state}={count}" for state, count in sorted(counts.items()))
        lines.append(f"- {actor.direction or actor.agent_id}：实验 {count_text or '0'}。")
        report = reports.get(actor.agent_id)
        if report and report.get("message"):
            lines.append("  最后报告：" + str(report["message"])[:1000])
        successful = [job for job in item["recent"] if job["state"] == "succeeded"]
        for job in successful[-3:]:
            metric_text = "、".join(
                f"{name}={value}" for name, value in sorted(job["metrics"].items())
            )
            lines.append(
                f"  成功实验 {job['job_id']}"
                + (f"：{metric_text}" if metric_text else "（未发布标量指标）")
            )
    return "\n".join(lines)


def _complete_closeout(c: Coordinator, summary: str, *, key: str) -> None:
    closeout = c.state.get("passk_closeout")
    if not closeout or closeout.get("status") == "completed":
        return
    closeout.update(status="completed", summary=summary)
    c.state["status"] = "succeeded"
    c.state["waiting_for_user"] = []
    supervisor = c.actor("rsi")
    supervisor.state, supervisor.wait_reason, supervisor.error = (
        AgentState.STOPPED,
        "passk: summary completed",
        None,
    )
    c.save_actor(supervisor)
    c.save()
    run = c.store.run()
    run.state, run.finished_at = RunState.SUCCEEDED, now()
    c.store.save_run(run)
    c.enqueue("rsi", "user", summary, key=key)
    c.store.event("run_completed", summary=summary, closeout="passk")


class PassKEngine(ResearchEngine):
    def _begin_closeout(self, reason: str) -> None:
        c = self.coordinator
        if c.state.get("passk_closeout"):
            return
        for actor in c.store.run().research_agents:
            if actor.state != AgentState.STOPPED:
                c.stop_agent(actor.agent_id, reason)
        c.state["passk_closeout"] = {
            "status": "pending",
            "reason": reason,
            "started_at": now(),
            "fallback_at": self.clock() + CLOSEOUT_FALLBACK_SECONDS,
        }
        c.state["budget_stop_reason"] = reason
        c.save()
        c.enqueue(
            "system",
            "rsi",
            "研究时间已到。研究员和新实验已停止。请调用 get_state，逐一检查每位研究员的"
            "最后报告，汇总实际完成的实验、最佳成绩与对应方案、失败或未完成项及限制。"
            "随后调用 finish_research；不要继续研究、启动研究员或提交实验。没有平台可信"
            "验证器时，明确说明成绩来自现有实验脚本，不要伪装成正式验证记录。",
            key="passk-closeout",
        )
        c.store.event("passk_closeout_started", reason=reason)

    def _wall_reason(self) -> str | None:
        if self.coordinator.state.get("passk_closeout"):
            return None
        return super()._wall_reason()

    async def tick(self) -> int:
        c = self.coordinator
        self._acquire()
        reason = self._wall_reason()
        if c.state["status"] == "running" and reason is not None:
            self._begin_closeout(reason)
        if c.state["status"] == "running" and self._wall_reason() is None:
            closeout = c.state.get("passk_closeout")
            retry = c.state.get("passk_retries", {}).get("rsi", {})
            if (
                closeout
                and closeout["status"] == "pending"
                and (
                    (
                        c.actor("rsi").state == AgentState.FAILED
                        and retry.get("count", 0) >= CLOSEOUT_MAX_RETRIES
                    )
                    or self.clock() >= closeout.get("fallback_at", float("inf"))
                )
            ):
                _complete_closeout(c, _fallback_closeout_summary(c), key="passk-closeout-fallback")
                return await super().tick()
            if (
                closeout
                and closeout["status"] == "pending"
                and "rsi" not in c.state["active"]
                and not c.state["inboxes"].get("rsi")
                and self.clock() >= closeout.get("retry_at", 0)
            ):
                attempt = closeout.get("retry_count", 0) + 1
                closeout.update(retry_count=attempt, retry_at=self.clock() + 60)
                c.save()
                c.enqueue(
                    "system",
                    "rsi",
                    "收尾尚未完成。请调用 get_state 汇总现有报告，然后使用 finish_research "
                    "提交最终总结；不要继续研究或提交实验。",
                    key=f"passk-closeout-retry:{attempt}",
                )
            retries = c.state.setdefault("passk_retries", {})
            for actor in (c.actor("rsi"), *c.store.run().research_agents):
                if actor.state != AgentState.FAILED:
                    continue
                entry = retries.setdefault(actor.agent_id, {"count": 0, "at": None})
                if actor.agent_id == "rsi" and closeout and entry["count"] >= CLOSEOUT_MAX_RETRIES:
                    _complete_closeout(
                        c, _fallback_closeout_summary(c), key="passk-closeout-fallback"
                    )
                    return await super().tick()
                if entry["at"] is None:
                    entry["at"] = self.clock() + min(60, 2 ** min(entry["count"] + 1, 6))
                    c.save()
                elif self.clock() >= entry["at"]:
                    # Recovery continues the same attempt and native history;
                    # it neither replaces its sample nor changes its direction.
                    c.retry_agent(actor.agent_id)
                    entry.update(count=entry["count"] + 1, at=None)
                    c.save()
            if (
                "rsi" not in c.state["active"]
                and not c.state["inboxes"].get("rsi")
                and (
                    not c.state.get("passk_ready")
                    or any(
                        needs_continue(c, actor.agent_id) for actor in c.store.run().research_agents
                    )
                )
                and self.clock() >= c.state.get("passk_supervision_at", 0) + 30
            ):
                c.state["passk_supervision_at"] = self.clock()
                c.enqueue(
                    "system",
                    "rsi",
                    "请检查研究状态：尚未启动时启动研究员，已停下来的研究员则提醒继续独立探索。",
                    key=f"passk-supervision:{self.clock()}",
                )
        return await super().tick()

    def _actor_allowed(self, agent_id: str) -> bool:
        if self.coordinator.state["status"] == "planned":
            return agent_id == "interaction"
        if self.coordinator.state.get("passk_closeout"):
            return self.coordinator.state["status"] == "running" and agent_id == "rsi"
        return self.coordinator.state["status"] == "running" and (
            agent_id == "rsi"
            or (agent_id != "interaction" and bool(self.coordinator.state.get("passk_ready")))
        )

    def _recovery_state(self, agent_id: str) -> dict[str, Any]:
        if agent_id == "interaction":
            return super()._recovery_state(agent_id)
        state = PassKActions(self.coordinator).state(agent_id)
        # Native history retains the task and evidence. Repeating the complete
        # node reference list after every tool/turn makes context grow quadratically.
        state.pop("brief", None)
        node = state.get("current_node")
        if node:
            state["current_node"] = {
                key: node[key] for key in ("iteration_id", "state", "assignment_ref")
            }
        return state

    def _complete_research_turn(
        self, agent_id: str, iteration_id: str | None, message_id: str, text: str
    ) -> None:
        c = self.coordinator
        PassKActions(c).progress(
            agent_id,
            "final:" + message_id,
            text.strip() or "本回合未给出文字总结，研究尚未结束。",
            [],
        )
        c.end_turn(agent_id)
        if c.state["status"] == "running":
            actor = c.actor(agent_id)
            actor.state, actor.wait_reason = AgentState.WAITING, "passk_supervision"
            c.save_actor(actor)
