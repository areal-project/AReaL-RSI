"""Independent complete attempts, using the existing stores, jobs and scheduler."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from areal_rsi.api.interfaces import ToolCall, ToolHandler, ToolResult
from areal_rsi.api.records import AgentState, RecordRef, RunState
from areal_rsi.api.research import (
    CandidateSnapshot,
    PublishedResult,
    ReportSummary,
    ResearchReport,
    RetryBackoff,
    TargetObservation,
    VerifiedResult,
)
from areal_rsi.infra.coordination.coordinator import Coordinator, effect_id
from areal_rsi.infra.coordination.engine import ResearchEngine
from areal_rsi.infra.coordination.tools.actions import (
    REFERENCES,
    STRING,
    ResearchActions,
    definition,
)
from areal_rsi.infra.storage.files import decode, encode, now

SUPERVISOR_TOOLS = [
    definition("start_researchers", "一次启动固定数量的独立研究员，每人获得相同的完整任务。"),
    definition("get_state", "查看研究员的进展、报告、正式成绩与剩余时间。"),
    definition(
        "continue_researcher",
        "提醒已停下来的研究员在原会话中继续；不附加研究建议。",
        agent_id=STRING,
    ),
    definition("wait", "结束本回合，等待研究员报告或系统提醒。", reason=STRING),
]
PROGRESS_TOOL = definition(
    "submit_report",
    "向 RSI 简要报告进展、结果或困难，报告后继续独立研究，不等待回复。",
    message=STRING,
    references=REFERENCES,
)
CONTINUE_MESSAGE = "研究尚未结束，还有时间和资源。请继续独立探索并验证你的方案。"

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
    team_read = False

    def definitions(self, agent_id: str, *, include_hidden: bool = False) -> list[dict[str, Any]]:
        if self.c.state.status == "planned" and agent_id == "interaction":
            return super().definitions(agent_id, include_hidden=include_hidden)
        if agent_id == "rsi":
            return SUPERVISOR_TOOLS
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
            seed and candidate_id == self.c.state.seed_candidate_id
        ):
            raise ValueError("This candidate is not part of your attempt")
        return candidate

    def read_record(self, agent_id: str, uri: str) -> Any:
        if agent_id == "interaction" and self.c.state.status == "planned":
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
                    team_read = self.team_read and call.name in {
                        "read_candidate_file",
                        "import_candidate",
                        "job_status",
                        "read_job_log",
                        "read_job_output_file",
                    }
                    if not team_read and (candidate_id := call.arguments.get("candidate_id")):
                        self.candidate(agent_id, candidate_id, seed=call.name != "verify_candidate")
                    if not team_read and (job_id := call.arguments.get("job_id")):
                        job = self.c.store.research_jobs().get(job_id)
                        if job is None or job.agent_id != agent_id:
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
        budget = c.budget()
        if "clock" in budget:
            # Run setup is not part of a researcher's personal budget. Keep
            # the wall-clock duration in the operator view, not this countdown.
            budget.pop("elapsed_seconds")
            if c.store.brief().limits.max_wall_seconds is None:
                for name in ("remaining_wall_seconds", "deadline_at"):
                    budget.pop(name)
        if agent_id == "rsi":
            return {
                "run_id": c.store.run_id,
                "agent_id": "rsi",
                "status": c.state.status,
                "brief": encode(c.store.brief()),
                "started_researchers": bool(c.state.passk_ready),
                "plan": c.state.plan,
                "budget": budget,
                "researchers": [
                    {
                        "agent_id": actor.agent_id,
                        "state": actor.state,
                        "wait_reason": actor.wait_reason,
                        "needs_continue": needs_continue(c, actor.agent_id),
                        "best": self.state(actor.agent_id)["best"],
                        "latest_report": encode(c.state.passk_reports.get(actor.agent_id)),
                    }
                    for actor in c.store.run().research_agents
                ],
            }
        actor = c.actor(agent_id)
        effective = budget.get("researchers", {}).get(agent_id)
        results = [
            result
            for result in c.store.records("verifications", VerifiedResult)
            if c.store.get("candidates", result.candidate_id, CandidateSnapshot).source_agent_id
            == agent_id
            and result.status == "succeeded"
            and result.passed
            and result.phase == "search"
            and result.contract_digest == c.store.contract_digest()
            and result.simulated == c.state.simulated
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
            "status": c.state.status,
            "brief": encode(c.store.brief()),
            "simulated": c.state.simulated,
            "current_node": encode(actor.iterations[-1]) if actor.iterations else None,
            "seed_candidate_id": c.state.seed_candidate_id,
            "workspace_candidate_id": self.workspace_candidate(agent_id),
            "best": encode(results[0]) if results else None,
            "budget": {
                name: budget[name]
                for name in (
                    "observed_at",
                    "clock",
                    "elapsed_seconds",
                    "remaining_wall_seconds",
                    "deadline_at",
                )
                if name in budget
            }
            | ({"effective_time": effective} if effective else {}),
            "execution_limits": {
                "max_concurrent_jobs": c.store.brief().limits.max_jobs,
                "submission_limit": None,
                "note": "max_jobs 是同时运行的作业数量，不是累计提交次数；本次提交次数不限。",
            },
        }

    async def _apply(self, agent_id: str, key: str, name: str, args: dict[str, Any]) -> ToolResult:
        if name == "start_researchers":
            await launch(self.c, self)
            return ToolResult(self.state("rsi"))
        if name == "continue_researcher":
            target = args["agent_id"]
            if target not in {actor.agent_id for actor in self.c.store.run().research_agents}:
                raise ValueError("Unknown researcher")
            if not needs_continue(self.c, target):
                return ToolResult({"continued": False, "reason": "研究员正在执行或等待自己的作业"})
            message_id = self.c.enqueue("rsi", target, CONTINUE_MESSAGE, key=key)
            return ToolResult({"continued": True, "message_id": message_id})
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
                simulated_run=self.c.state.simulated,
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
        c.state.passk_reports[agent_id] = ReportSummary(
            node.report_ref.uri, report.message[:4000], report.created_at
        )
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
        c.state.status == "running"
        and actor.state == AgentState.WAITING
        and actor.wait_reason == "passk_supervision"
        and agent_id not in c.state.active
        and not c.state.inboxes.get(agent_id)
    )


async def launch(c: Coordinator, actions: PassKActions) -> None:
    """RSI invokes this fixed launch; no sampled assignments or replacement attempts."""
    if c.state.passk_ready:
        return
    for agent_id in ("interaction",):
        actor = c.actor(agent_id)
        actor.state, actor.wait_reason = AgentState.STOPPED, "passk: no intervention"
        c.save_actor(actor)
        c.state.inboxes[agent_id] = []
        c.state.active.pop(agent_id, None)
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
    c.state.passk_ready = True
    c.save()
    c.store.event("passk_launched", k=brief.limits.max_agents)


def stop_on_target(c: Coordinator) -> None:
    contract = c.store.brief().verification
    best = c.store.run().best_result_ref
    if c.state.status != "running" or not contract or contract.target is None or not best:
        return
    result = c.store.public_record(best)
    score = result["score"]
    reached = (
        score >= contract.target if contract.direction == "maximize" else score <= contract.target
    )
    if reached:
        c.state.passk_target = TargetObservation(decode(PublishedResult, result), now())
        c.save()
        c.stop("已有独立尝试通过正式验证并达到目标，停止整组探索")
        if contract.identity.get("require_final_confirmation"):
            return  # Search success is provisional until the independent final passes.
        c.state.status = "succeeded"
        c.save()
        run = c.store.run()
        run.state = RunState.SUCCEEDED
        c.store.save_run(run)


class PassKEngine(ResearchEngine):
    def _check_effective_time(self) -> None:
        if self.effective is None:
            return
        c = self.coordinator
        at = self.clock()
        if at - self.effective.data.heartbeat_at < 1:
            return
        self.effective.data.heartbeat_at = at
        limit = c.store.brief().limits.max_effective_seconds
        assert limit is not None
        for actor in c.store.run().research_agents:
            if actor.state == AgentState.STOPPED:
                self.effective.finish_agent(actor.agent_id, at)
                continue
            budget = self.effective.budget(actor.agent_id, at, limit)
            if budget["effective_seconds"] >= limit:
                self.effective.finish_agent(actor.agent_id, at)
                c.stop_agent(actor.agent_id, "个人有效研究时间已用完")
                c.enqueue(
                    "system",
                    "rsi",
                    f"{actor.agent_id} 的有效研究时间已用完，已停止；其他研究员继续。",
                    key="effective-stop:" + actor.agent_id,
                )
                c.store.event("researcher_time_exhausted", agent_id=actor.agent_id, budget=budget)
        c.save()
        agents = c.store.run().research_agents
        if agents and all(actor.state == AgentState.STOPPED for actor in agents):
            c.stop("所有研究员的有效时间已用完，停止探索并统一复测")

    async def tick(self) -> int:
        c = self.coordinator
        self._acquire()
        if c.state.status == "running":
            self._check_effective_time()
        if c.state.status == "running" and self._wall_reason() is None:
            retries = c.state.passk_retries
            for actor in (c.actor("rsi"), *c.store.run().research_agents):
                if actor.state != AgentState.FAILED:
                    continue
                entry = retries.setdefault(actor.agent_id, RetryBackoff())
                if entry.at is None:
                    entry.at = self.clock() + min(60, 2 ** min(entry.count + 1, 6))
                    c.save()
                elif self.clock() >= entry.at:
                    # Recovery continues the same attempt and native history;
                    # it neither replaces its sample nor changes its direction.
                    c.retry_agent(actor.agent_id)
                    entry.count, entry.at = entry.count + 1, None
                    c.save()
            for actor in c.store.run().research_agents:
                if needs_continue(c, actor.agent_id):
                    report = c.state.passk_reports[actor.agent_id]
                    c.enqueue(
                        "system",
                        actor.agent_id,
                        CONTINUE_MESSAGE,
                        key="passk-continue:" + report.uri,
                    )
            if (
                "rsi" not in c.state.active
                and not c.state.inboxes.get("rsi")
                and not c.state.passk_ready
                and self.clock() >= c.state.passk_supervision_at + 30
            ):
                c.state.passk_supervision_at = self.clock()
                c.enqueue(
                    "system",
                    "rsi",
                    "请检查研究状态，启动研究员。",
                    key=f"passk-supervision:{self.clock()}",
                )
        return await super().tick()

    def _actor_allowed(self, agent_id: str) -> bool:
        if self.coordinator.state.status == "planned":
            return agent_id == "interaction"
        return self.coordinator.state.status == "running" and (
            agent_id == "rsi"
            or (agent_id != "interaction" and bool(self.coordinator.state.passk_ready))
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
        if c.state.status == "running":
            actor = c.actor(agent_id)
            actor.state, actor.wait_reason = AgentState.WAITING, "passk_supervision"
            c.save_actor(actor)
