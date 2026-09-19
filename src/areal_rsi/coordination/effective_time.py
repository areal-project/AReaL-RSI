"""Baseline active time: worker periods minus the union of API outages/GPU queues.

Each researcher owns its clock. Normal inference, compilation and GPU execution
count; another researcher's waits never extend this researcher's budget.
"""

from __future__ import annotations

from typing import Any

PROTOCOL = "baseline-active-v1"


def union_seconds(spans: list[tuple[float, float | None]], start: float, end: float) -> float:
    cursor, total = start, 0.0
    for lo, hi in sorted(
        (max(start, float(a)), min(end, end if b is None else b)) for a, b in spans
    ):
        total += max(0.0, hi - max(lo, cursor))
        cursor = max(cursor, hi)
    return total


class EffectiveTime:
    def __init__(self, state: dict[str, Any]):
        self.data = state.setdefault(
            "effective_time", {"protocol": PROTOCOL, "periods": [], "agents": {}}
        )

    def open_worker(self, at: float) -> None:
        periods = self.data["periods"]
        if periods and periods[-1][1] is None:
            periods[-1][1] = self.data["heartbeat_at"]
        periods.append([at, None])
        self.data["heartbeat_at"] = at

    def close_worker(self, at: float) -> None:
        periods = self.data["periods"]
        if periods and periods[-1][1] is None:
            periods[-1][1] = at
        self.data["heartbeat_at"] = at

    def start_agent(self, agent_id: str, at: float) -> None:
        self.data["agents"].setdefault(
            agent_id,
            {
                "started_at": at,
                "finished_at": None,
                "api_spans": [],
                "queue_spans": {},
            },
        )

    def availability(self, agent_id: str, available: bool, at: float) -> None:
        agent = self.data["agents"].get(agent_id)
        if agent is None or agent["finished_at"] is not None:
            return
        spans = agent["api_spans"]
        opened = bool(spans and spans[-1][1] is None)
        if available and opened:
            spans[-1][1] = at
        elif not available and not opened:
            spans.append([at, None])

    def queue(self, agent_id: str, key: str, start: float, end: float | None) -> None:
        agent = self.data["agents"].get(agent_id)
        if agent is not None and agent["finished_at"] is None:
            agent["queue_spans"][key] = [start, end]

    def finish_agent(self, agent_id: str, at: float) -> None:
        agent = self.data["agents"].get(agent_id)
        if agent is not None and agent["finished_at"] is None:
            agent["finished_at"] = at

    def budget(self, agent_id: str, at: float, limit: float) -> dict[str, Any]:
        agent = self.data["agents"].get(agent_id)
        worker = queue = api = excluded = 0.0
        if agent is not None:
            end = min(at, agent["finished_at"] or at)
            queues = list(agent["queue_spans"].values())
            for lo, hi in self.data["periods"]:
                start, stop = max(lo, agent["started_at"]), min(hi or end, end)
                worker += max(0.0, stop - start)
                queue += union_seconds(queues, start, stop)
                api += union_seconds(agent["api_spans"], start, stop)
                excluded += union_seconds(queues + agent["api_spans"], start, stop)
        active = max(0.0, worker - excluded)
        return {
            "clock": PROTOCOL,
            "limit_seconds": limit,
            "worker_wall_seconds": worker,
            "gpu_queue_seconds": queue,
            "api_wait_seconds": api,
            "excluded_union_seconds": excluded,
            "effective_seconds": active,
            "remaining_effective_seconds": max(0.0, limit - active),
            "started_at": agent["started_at"] if agent else None,
            "finished_at": agent["finished_at"] if agent else None,
        }
