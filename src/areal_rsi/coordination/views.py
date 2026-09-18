"""Small model-facing views over complete, durable research records.

Storage and the workbench keep the full history. A model normally needs the
current task and new findings; older records remain available by reference.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from areal_rsi.api.base import RecordRef
from areal_rsi.api.research import CandidateSnapshot, EvidenceRecord
from areal_rsi.base.storage import encode

if TYPE_CHECKING:
    from areal_rsi.coordination.coordinator import Coordinator


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
    result["seed_candidate_id"] = c.state["seed_candidate_id"]
    seed_id = c.state["seed_candidate_id"]
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
        **{key: value[key] for key in ("status", "score", "shared") if key in value},
    }
