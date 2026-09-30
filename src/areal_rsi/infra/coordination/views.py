"""Read-only research projections for model context and the workbench."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from areal_rsi.api.control import public_tool_text
from areal_rsi.api.records import RecordRef, ResearchRun
from areal_rsi.api.research import (
    AgentTraceEntry,
    AgentTracePage,
    CandidateSnapshot,
    EvidenceRecord,
    GoalTurn,
    ResearchMessage,
    ResearchReport,
)
from areal_rsi.infra.coordination.coordinator import Coordinator
from areal_rsi.infra.coordination.store import ResearchStore
from areal_rsi.infra.storage.files import encode
from areal_rsi.infra.storage.runs import FileRunStore

if TYPE_CHECKING:
    from areal_rsi.infra.coordination.coordinator import Coordinator


def agent_trace(
    c: Coordinator, agent_id: str, *, before: int | None = None, limit: int = 50,
) -> AgentTracePage:
    if agent_id not in {"interaction", "rsi", *(a.agent_id for a in c.store.run().research_agents)}:
        raise ValueError("Agent 不属于当前任务")
    # Bounded scanning also paginates through intervals where only peers were active.
    window = c.store.events(limit=max(100, limit), before=before)
    completed = False
    normal_endings: set[int] = set()
    for event in window:
        if event.agent_id != agent_id:
            continue
        if event.kind == "agent_running":
            completed = False
        elif event.kind == "goal_updated":
            goal = event.data.get("goal")
            if isinstance(goal, dict):
                completed = goal.get("status") == "completed"
        elif (event.kind == "agent_suspended" and completed
              and event.data.get("reason") == "worker_interrupted"):
            normal_endings.add(event.sequence)
    entries = []
    next_before = window[0].sequence if window and window[0].sequence > 1 else None
    for event in reversed(window):
        data = event.data
        own_message = ((data.get("recipient") == agent_id and data.get("sender") == "user")
                       or (data.get("sender") == agent_id and data.get("recipient") == "user"))
        if event.agent_id != agent_id and not own_message:
            continue
        entry = AgentTraceEntry(event.sequence, event.created_at, event.kind)
        if event.kind == "assistant_output":
            entry.text = public_tool_text(data.get("text"))
        elif event.kind in {"tool_started", "tool_completed"}:
            entry.name = str(data.get("name", "tool"))
            entry.call_id = str(data.get("call_id", event.sequence))
            entry.arguments = public_tool_text(data.get("arguments"))
            entry.result = public_tool_text(data.get("result"))
            entry.status = str(data.get("status") or (
                "running" if event.kind == "tool_started" else "completed"))
            started = data.get("started_at")
            entry.started_at = started if isinstance(started, str) else None
        elif own_message:
            entry.kind = "user_message" if data.get("sender") == "user" else "agent_message"
            entry.text = public_tool_text(data.get("text"))
        elif event.kind == "api_availability":
            entry.text = ("运行时报告模型服务可用" if data.get("available")
                          else "模型服务暂不可用，等待重试")
        elif event.kind == "goal_updated":
            entry.text = public_tool_text(data.get("summary"))
        elif event.kind in {"agent_failed", "agent_suspended", "agent_running"}:
            reason = data.get("reason")
            explanations = {"worker_interrupted": "本次执行已中断，上下文已保存",
                            "human_paused": "用户暂停了此研究员", "run_paused": "任务已暂停"}
            entry.text = public_tool_text(data.get("error") or explanations.get(str(reason))
                                          or reason or "开始执行")
            if event.sequence in normal_endings:
                entry.kind, entry.text = "agent_finished", "目标已完成，结束本轮执行"
        elif event.kind in {"job_submitted", "job_completed"}:
            job = data.get("job")
            if isinstance(job, dict):
                entry.job_id = str(job.get("job_id", ""))
                entry.text = f"作业 {entry.job_id} · {job.get('state', '')}"
        else:
            continue
        entries.append(entry)
        if len(entries) == limit:
            next_before = event.sequence if event.sequence > 1 else None
            break
    return AgentTracePage(agent_id, list(reversed(entries)), next_before)


def evidence_preview(item: EvidenceRecord, uri: str) -> dict[str, Any]:
    return {
        "evidence_id": item.evidence_id,
        "title": item.title[:160],
        "summary": item.summary[:320],
        "limitations": item.limitations[:160],
        "shared": item.shared,
        "uri": uri,
        "truncated": len(item.summary) > 320 or len(item.limitations) > 160,
        "summary_chars": len(item.summary),
    }


def model_state(c: Coordinator, agent_id: str) -> dict[str, Any]:
    """Return current work, without copying past iterations into the context."""
    result = c.summary()
    result["collaborators"] = c.collaborators(agent_id)
    agents = result["agents"]
    if agent_id != "rsi":
        result.pop("pending_report_replies", None)
    if agent_id not in {"rsi", "interaction"}:
        agents = [a for a in agents if a["agent_id"] == agent_id]
        result.pop("waiting_for_user", None)
    result["agents"] = []
    for agent in agents:
        current = next(
            (
                node
                for node in agent["iterations"]
                if node["iteration_id"] == agent["current_iteration_id"]
            ),
            None,
        )
        result["agents"].append(
            {
                **{
                    key: agent[key]
                    for key in (
                        "agent_id",
                        "direction",
                        "state",
                        "current_iteration_id",
                        "wait_reason",
                    )
                },
                "error": (agent.get("error") or "")[:500] or None,
                "iteration_count": len(agent["iterations"]),
                "assignment_ref": current.get("assignment_ref") if current else None,
                "report_ref": current.get("report_ref") if current else None,
            }
        )
    evidence = sorted(c.store.evidence(agent_id), key=lambda item: item.created_at, reverse=True)
    result["evidence_count"] = len(evidence)
    result["evidence"] = [
        evidence_preview(
            item,
            c.store.paths.reference(c.store.root / "evidence" / f"{item.evidence_id}.json").uri,
        )
        for item in evidence[:5]
    ]
    result["evidence_has_more"] = len(evidence) > 5
    result["brief_ref"] = encode(c.store.run().brief_ref)
    result["seed_candidate_id"] = c.state.seed_candidate_id
    seed_id = c.state.seed_candidate_id
    if seed_id:
        seed = c.store.get("candidates", seed_id, CandidateSnapshot)
        result["seed_candidate"] = {
            **encode(seed),
            "uri": c.store.paths.reference(c.store.root / "candidates" / f"{seed_id}.json").uri,
        }
    return result


def record_preview(
    value: dict[str, Any], ref: RecordRef, *, source_job: dict[str, Any] | None = None
) -> dict[str, Any]:
    """A discovery result is a pointer, not another copy of the whole record."""
    text = next(
        (
            value[key]
            for key in (
                "title",
                "message",
                "description",
                "instruction",
                "interpretation",
                "summary",
                "reason",
                "query",
                "url",
            )
            if isinstance(value.get(key), str) and value[key]
        ),
        "",
    )
    if not text and value.get("job_id") and value.get("path"):
        job = source_job or {}
        context = job.get("command_name") or "作业输出"
        if job.get("parameters"):
            context += " " + json.dumps(job["parameters"], ensure_ascii=False)[:120]
        text = f"{value['job_id']} · {value['path']} · {context}"
    record_path = urlsplit(ref.uri).path.lstrip("/").removesuffix(".json")
    return {
        "uri": ref.uri,
        "record_id": record_path
        if record_path.startswith("withdrawals/")
        else record_path.rsplit("/", 1)[-1],
        "created_at": value.get("created_at"),
        "summary": text[:240],
        # Older output excerpts have no archival timestamp. Keep that unknown
        # while exposing the producing job's real date for discovery/sorting.
        **({"job_created_at": source_job.get("created_at")} if source_job else {}),
        **{
            key: value[key]
            for key in ("status", "score", "shared", "agent_id", "source_agent_id", "candidate_id")
            if key in value
        },
    }


def communications(
    store: ResearchStore,
    messages: list[ResearchMessage],
    nodes: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
    delivered: set[str],
) -> list[dict[str, Any]]:
    """Attribute explicit same-run references, never similarities in message prose."""
    owners = {node["iteration_id"]: node["agent_id"] for node in nodes}
    researchers = set(owners.values())
    job_owners = {job["job_id"]: job["agent_id"] for job in jobs}
    candidates = {job["candidate_id"]: job["agent_id"] for job in jobs if job.get("candidate_id")}
    assignments = {(node["agent_id"], node["assignment"].get("instruction")) for node in nodes}
    reports = {
        f"run://{store.run_id}/reports/{report['report_id']}.json": report
        for node in nodes
        for report in node.get("reports", [])
    }
    cache: dict[str, dict[str, Any] | None] = {}

    def source(uri: str) -> dict[str, Any] | None:
        if uri in cache:
            return cache[uri]
        cache[uri] = None
        # public_record enforces the current run and public-record boundary.
        try:
            record = store.public_record(RecordRef(uri))
        except (ValueError, FileNotFoundError):
            return None
        owner = (
            job_owners.get(record.get("job_id"))
            or record.get("agent_id")
            or record.get("source_agent_id")
            or owners.get(record.get("iteration_id"))
            or candidates.get(record.get("candidate_id"))
            or job_owners.get(record.get("result_id"))
            or job_owners.get(record.get("job_id"))
        )
        if owner not in researchers:
            return None
        cache[uri] = {
            "agent_id": owner,
            "uri": uri,
            "created_at": record.get("created_at"),
            "summary": str(
                record.get("message")
                or record.get("summary")
                or record.get("description")
                or record.get("title")
                or ""
            )[:800],
        }
        return cache[uri]

    result = []
    for message in sorted(messages, key=lambda item: (item.created_at, item.message_id)):
        if not (
            (message.sender == "rsi" and message.recipient in researchers)
            or (message.sender in researchers and message.recipient in researchers | {"rsi"})
        ):
            continue
        if message.sender == "rsi" and (message.recipient, message.text) in assignments:
            continue  # The agent's task card already shows its initial assignment.
        refs = [ref.uri for ref in message.references]
        report = next((reports[uri] for uri in refs if uri in reports), None)
        result.append(
            {
                "message_id": message.message_id,
                "kind": "advice" if message.sender == "rsi" else "report" if report else "message",
                "sender": message.sender,
                "recipient": message.recipient,
                "text": message.text,
                "created_at": message.created_at,
                "delivery_status": "delivered" if message.message_id in delivered else "pending",
                "references": [{"uri": uri} for uri in refs],
                "sources": [item for uri in refs if (item := source(uri)) is not None],
                "report_id": report["report_id"] if report and message.sender != "rsi" else None,
            }
        )
    return result


def research_detail(
    c: Coordinator,
    *,
    after: int = 0,
    event_limit: int | None = None,
    goal_before: int | None = None,
    goal_limit: int = 20,
    model_settings: dict[str, Any],
    collaboration: dict[str, Any],
    worker_alive: bool,
    active_agents: tuple[str, ...],
) -> dict[str, Any]:
    summary = c.summary()
    summary["finalization"] = encode(c.completion)
    run = c.store.run()
    actors = {actor["agent_id"]: actor for actor in summary["agents"]}
    evidence = encode(c.store.records("evidence", EvidenceRecord))
    evidence_by_iteration: dict[str, list[dict[str, Any]]] = {}
    for item in evidence:
        if item["iteration_id"] is not None:
            evidence_by_iteration.setdefault(item["iteration_id"], []).append(item)
    all_messages = c.store.records("messages", ResearchMessage)
    report_messages = {
        ref.uri: message
        for message in all_messages
        if message.recipient == "rsi"
        for ref in message.references
        if "/reports/" in ref.uri
    }
    reports_by_iteration: dict[str, list[dict[str, Any]]] = {}
    reports_by_message = {}
    delivered = set(c.state.delivered)
    for report_record in sorted(
        c.store.records("reports", ResearchReport),
        key=lambda item: (item.created_at, item.report_id),
    ):
        report_view = encode(report_record)
        ref = c.store.paths.reference(c.store.root / "reports" / f"{report_record.report_id}.json")
        message = report_messages.get(ref.uri)
        report_view["delivery_status"] = (
            "delivered"
            if message and message.message_id in delivered
            else "pending"
            if message
            else "archived"
        )
        reports_by_iteration.setdefault(report_record.iteration_id, []).append(report_view)
        if message:
            reports_by_message[message.message_id] = (report_record, message.sender)
    nodes, edges = [], []
    for actor in run.research_agents:
        for node in actor.iterations:
            assignment = c.store.public_record(node.assignment_ref)
            report = c.store.public_record(node.report_ref) if node.report_ref else None
            nodes.append(
                {
                    **encode(node),
                    "assignment": assignment,
                    "goal": encode(c.state.goals.get(actor.agent_id)),
                    "report": report,
                    "reports": reports_by_iteration.get(node.iteration_id, []),
                    "evidence": evidence_by_iteration.get(node.iteration_id, []),
                    "verification": None,
                }
            )
            if node.parent_iteration_id:
                edges.append(
                    {
                        "source": node.parent_iteration_id,
                        "target": node.iteration_id,
                        "kind": "parent",
                    }
                )
            for ref in node.references:
                try:
                    source = c.store.public_record(ref)
                except (ValueError, FileNotFoundError):
                    continue
                source_node = source.get("iteration_id")
                if source_node and source_node != node.iteration_id:
                    edges.append(
                        {
                            "source": source_node,
                            "target": node.iteration_id,
                            "kind": "reference",
                        }
                    )
    archived_chat = set(c.state.archived_chat_message_ids)
    messages = [
        encode(m)
        for m in all_messages
        if (m.sender == "user" or m.recipient == "user") and m.message_id not in archived_chat
    ]
    messages.sort(key=lambda m: m["created_at"])
    answered = {m.reply_to for m in all_messages if m.recipient == "user" and m.reply_to}
    for message in messages:
        if message["sender"] == "user":
            identifier = message["message_id"]
            message["delivery_status"] = (
                "replied" if identifier in answered else
                "delivered" if identifier in c.state.human.received_messages
                or identifier in c.state.delivered else "pending"
            )
    jobs_path = c.store.root / "coordination" / "jobs.json"
    jobs = []
    contract = c.store.brief().verification
    contract_digest = c.store.contract_digest() if contract else None
    withdrawn: dict[str, bool] = {}
    if jobs_path.exists():
        from areal_rsi.infra.execution.progress import read_progress

        intents = json.loads(jobs_path.read_text())
        for job_id, info in intents.items():
            path = c.store.root / "jobs" / job_id / "job.json"
            if not path.exists():
                continue
            record = json.loads(path.read_text())
            baseline = bool(
                info.get("verification")
                and info.get("candidate_id")
                and info["candidate_id"] == c.state.baseline_candidate_id
                and info.get("phase") == "search"
            )
            phase = info.get("phase")
            title = (
                "起点评测"
                if baseline
                else {"search": "迭代验证", "final": "最终验证", "diagnostic": "诊断检查"}.get(
                    phase, "验证作业"
                )
                if info.get("verification")
                else "研究执行"
            )
            job_actor = actors[info["agent_id"]]
            jobs.append(
                {
                    "job_id": job_id,
                    "title": title,
                    "phase": phase,
                    "verification": bool(info.get("verification")),
                    "is_baseline": baseline,
                    "iteration_id": info.get("iteration_id"),
                    "candidate_id": info.get("candidate_id"),
                    "agent_id": info["agent_id"],
                    "agent_name": job_actor["direction"]
                    or {"rsi": "RSI Agent", "interaction": "Interaction Agent"}.get(
                        info["agent_id"], "Research Agent"
                    ),
                    "status": record["state"],
                    "created_at": record["created_at"],
                    "gpu_ids": record["assigned_gpu_ids"],
                    "started_at": record["started_at"],
                    "finished_at": record["finished_at"],
                    "exit_code": record["exit_code"],
                    "error": record["error"],
                    "simulated": c.state.simulated,
                    "result": info.get("result"),
                    "published_candidate_id": info.get("published_candidate_id"),
                    "verification_job_id": info.get("verification_job_id"),
                    "source_job_id": info.get("source_job_id"),
                    "goal_revision": info.get("goal_revision", 0),
                    "publication_error": info.get("publication_error"),
                    "report_available": bool(info.get("verification") and info.get("processed")
                                             and info.get("public_output_subdir") is not None),
                    "progress": read_progress(Path(info["output"])),
                }
            )
            result = info.get("result")
            if (
                contract
                and info.get("verification")
                and not baseline
                and result
                and result.get("status") == "succeeded"
                and result.get("passed") is True
                and result.get("score") is not None
                and result.get("phase") == "search"
                and result.get("contract_digest") == contract_digest
                and result.get("goal_revision", 0) == c.store.brief().revision
                and result.get("simulated") == c.state.simulated
            ):
                candidate_id = result["candidate_id"]
                if candidate_id not in withdrawn:
                    withdrawn[candidate_id] = c.store.is_withdrawn(candidate_id)
                if withdrawn[candidate_id]:
                    continue
                jobs[-1]["search_eligible"] = True
                for public_node in nodes:
                    if public_node["iteration_id"] == info.get("iteration_id"):
                        previous = public_node["verification"]
                        if previous is None or (
                            result["score"] > previous["score"]
                            if contract.direction == "maximize"
                            else result["score"] < previous["score"]
                        ):
                            public_node["verification"] = result
    events = c.store.events(after, limit=event_limit)
    if c.state.mode == "passk":
        # Older runs have the durable report/message, but no named report event.
        # Reuse its original cursor and timestamp; never rewrite archived events.
        for index, event in enumerate(events):
            message_id = event.data.get("message_id")
            report_message = (
                reports_by_message.get(message_id) if isinstance(message_id, str) else None
            )
            if event.kind == "message" and report_message:
                report_record, sender = report_message
                events[index] = replace(
                    event,
                    kind="report_submitted",
                    agent_id=sender,
                    iteration_id=report_record.iteration_id,
                    data={**event.data, "report_id": report_record.report_id},
                )
    turn_files = sorted((c.store.root / "goal-turns").glob("*.json"), reverse=True)
    turn_count = len(turn_files)
    if goal_before is not None:
        turn_files = [path for path in turn_files if int(path.stem) < goal_before]
    turns = [c.store.get("goal-turns", path.stem, GoalTurn) for path in turn_files[:goal_limit]]
    return {
        **summary,
        "goal_turns": encode(turns),
        "goal_turns_total": turn_count,
        "goal_turns_next": turns[-1].number if len(turn_files) > goal_limit else None,
        "objective": c.store.brief().objective,
        "communications": communications(c.store, all_messages, nodes, jobs, delivered),
        "nodes": nodes,
        "edges": edges,
        "messages": messages,
        "agent_activity": encode(c.state.agent_activity),
        "workspace_dir": c.state.workspace_dir,
        "worktree_dirs": {
            agent.agent_id: str(path)
            for agent in run.research_agents
            if (path := c.store.root.parent.parent / "worktrees"
                / c.store.run_id / agent.agent_id).is_dir()
        } if c.state.workspace_dir else {},
        "execution_setup": c.state.execution_setup,
        "preparation": encode(c.state.preparation),
        "human": {
            "paused_agents": c.state.human.paused_agents,
            "verification_current": c.goal_verification_current,
            "assisted": bool(c.state.human.goal_changes or any(
                m.sender == "user" and m.recipient.startswith("research_")
                for m in all_messages)),
            "goal_changes": encode(c.state.human.goal_changes),
        },
        "model_settings": model_settings,
        "collaboration": collaboration,
        "jobs": jobs,
        "evidence": evidence,
        "final": c.store.public_record(run.final_result_ref) if run.final_result_ref else None,
        "events": encode(events),
        "event_cursor": events[-1].sequence if events else after,
        "worker_alive": worker_alive,
        "active_agents": list(active_agents),
        "pending_agents": c.pending(),
        "started_at": c.state.started_at,
        "finished_at": run.finished_at,
    }


def research_list(
    root: Path,
    coordinator: Callable[[str], Coordinator],
    *,
    allow_simulation: bool,
) -> dict[str, Any]:
    runs = FileRunStore(root).list(ResearchRun)
    # Workbench organization is independent of frozen experiment records.
    group_file = root / "workbench-groups.json"
    groups = json.loads(group_file.read_text())["groups"] if group_file.exists() else []
    run_groups = {
        run_id: {key: group[key] for key in ("id", "name", "created_at")}
        for group in groups
        for run_id in group["run_ids"]
    }

    def created_at(run: ResearchRun) -> str:
        c = coordinator(run.run_id)
        if c.state.created_at:
            return str(c.state.created_at)
        # Older runs predate the timestamp field. Their first immutable
        # event records creation, unlike file modification time.
        event = c.store.root / "events" / "000000000001.json"
        return (
            str(json.loads(event.read_text())["created_at"])
            if event.exists()
            else (run.started_at or "")
        )

    return {
        "runs": sorted(
            [
                {
                    "run_id": r.run_id,
                    "objective": r.plan.goal,
                    "status": coordinator(r.run_id).state.status,
                    "simulated": coordinator(r.run_id).state.simulated,
                    "created_at": created_at(r),
                    "group": run_groups.get(r.run_id),
                }
                for r in runs
                if (allow_simulation or not coordinator(r.run_id).state.simulated)
            ],
            key=lambda r: (r["created_at"], r["run_id"]),
            reverse=True,
        )
    }
