"""Read-only research projections for model context and terminal clients."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from areal_rsi.api.control import public_tool_text
from areal_rsi.api.records import RecordRef
from areal_rsi.api.research import (
    AgentTraceEntry,
    AgentTracePage,
    CandidateSnapshot,
    EvidenceRecord,
)
from areal_rsi.infra.coordination.coordinator import Coordinator
from areal_rsi.infra.storage.files import encode

if TYPE_CHECKING:
    from areal_rsi.infra.coordination.coordinator import Coordinator


def agent_trace(
    c: Coordinator,
    agent_id: str,
    *,
    before: int | None = None,
    limit: int = 50,
) -> AgentTracePage:
    if agent_id not in {"interaction", "rsi", *(a.agent_id for a in c.store.run().research_agents)}:
        raise ValueError("Agent 不属于当前任务")
    if not 1 <= limit <= 1000:
        raise ValueError("Trace limit must be between 1 and 1000")
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
        elif (
            event.kind == "agent_suspended"
            and completed
            and event.data.get("reason") == "worker_interrupted"
        ):
            normal_endings.add(event.sequence)
    entries = []
    next_before = window[0].sequence if window and window[0].sequence > 1 else None
    for event in reversed(window):
        data = event.data
        own_message = (data.get("recipient") == agent_id and data.get("sender") == "user") or (
            data.get("sender") == agent_id and data.get("recipient") == "user"
        )
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
            entry.status = str(
                data.get("status") or ("running" if event.kind == "tool_started" else "completed")
            )
            started = data.get("started_at")
            entry.started_at = started if isinstance(started, str) else None
        elif own_message:
            entry.kind = "user_message" if data.get("sender") == "user" else "agent_message"
            entry.text = public_tool_text(data.get("text"))
        elif event.kind == "api_availability":
            entry.text = (
                "运行时报告模型服务可用" if data.get("available") else "模型服务暂不可用，等待重试"
            )
        elif event.kind == "goal_updated":
            entry.text = public_tool_text(data.get("summary"))
        elif event.kind in {"agent_failed", "agent_suspended", "agent_running"}:
            reason = data.get("reason")
            explanations = {
                "worker_interrupted": "本次执行已中断，上下文已保存",
                "human_paused": "用户暂停了此研究员",
                "run_paused": "任务已暂停",
            }
            entry.text = public_tool_text(
                data.get("error") or explanations.get(str(reason)) or reason or "开始执行"
            )
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
