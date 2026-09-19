"""Read-only communication provenance for the operator's research graph."""

from __future__ import annotations

from typing import Any

from areal_rsi.api.base import RecordRef
from areal_rsi.api.research import ResearchMessage
from areal_rsi.coordination.store import ResearchStore


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
            record.get("agent_id")
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
