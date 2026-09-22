"""Read-only live progress probe; never sends messages or changes research state.

Run with --batch <operator/batch_*/batch.json>; add --watch for 30-second sampling.
Reports silence and observed output throughput, not the quality of research.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path


def read(path: Path) -> dict:
    return json.loads(path.read_text())


def timestamp(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


class Journal:
    """Follow complete appended records without repeatedly scanning old snapshots."""

    def __init__(self) -> None:
        self.offset = 0
        self.last_event = 0.0
        self.last_usage = 0.0
        self.last_type = ""
        self.usage: list[tuple[float, int]] = []
        self.pending: dict[str, dict] = {}

    def update(self, path: Path, now: float) -> None:
        with path.open("rb") as stream:
            stream.seek(self.offset)
            while line := stream.readline():
                if not line.endswith(b"\n"):
                    break
                self.offset = stream.tell()
                event = json.loads(line).get("event")
                if not event:
                    continue
                at, kind = timestamp(event["time"]), event["type"]
                self.last_event, self.last_type = at, kind
                data = event.get("data", {})
                if kind == "usage.updated":
                    self.last_usage = at
                    self.usage.append((at, data["usage"].get("outputTokens", 0)))
                if kind == "tool.started":
                    self.pending[data["callId"]] = {"name": data.get("name"), "at": at}
                elif kind in {"tool.completed", "tool.failed"}:
                    self.pending.pop(data["callId"], None)
        self.usage = [(at, n) for at, n in self.usage if at >= now - 600]


def probe(batch: dict, journals: dict[Path, Journal]) -> dict:
    now = time.time()
    rows = []
    active_runs = []
    for trial in batch["runs"]:
        run = Path(batch["data_root"]) / "runs" / trial["run_id"]
        state = read(run / "coordination/state.json")
        if state["status"] != "running" or state.get("user_stopped"):
            continue
        active_runs.append(trial["run_id"])
        jobs = [read(p) for p in (run / "jobs").glob("*/job.json")]
        for record in sorted((run / "agents").glob("*/agent.json")):
            actor = read(record)
            if not actor.get("direction") or actor["state"] != "running":
                continue
            agent, directory = actor["agent_id"], record.parent
            profile = state.get("researcher_profiles", {}).get(agent, {"name": "通用研究员"})
            sessions = directory / "sessions"
            observed = []
            for path in sessions.glob("*/runtime/data/state/journal.jsonl"):
                journal = journals.setdefault(path, Journal())
                journal.update(path, now)
                observed.append(journal)
            native = list(sessions.glob("*/runtime/codex-home/sessions/**/rollout*.jsonl"))
            started = (
                state.get("effective_time", {})
                .get("agents", {})
                .get(agent, {})
                .get("started_at", timestamp(state["started_at"]))
            )
            latest = max(observed, key=lambda j: j.last_event, default=None)
            last_activity = max(
                [started, *(j.last_event for j in observed), *(p.stat().st_mtime for p in native)]
            )
            silence = max(0.0, now - last_activity)
            pending = list(latest.pending.values()) if latest else []
            active_jobs = [
                j["job_id"]
                for j in jobs
                if j["spec"].get("metadata", {}).get("agent_id") == agent
                and j["state"] not in {"succeeded", "failed", "cancelled", "timed_out"}
            ]
            status = "active"
            if active_jobs:
                status = "job_wait"
            elif pending:
                status = "tool_wait"
            elif silence >= 600:
                status = "suspected_stall"
            elif silence >= 180:
                status = "quiet"
            rows.append(
                {
                    "run_id": trial["run_id"],
                    "label": trial["label"],
                    "agent_id": agent,
                    "profile": profile["name"],
                    "status": status,
                    "silence_seconds": round(silence, 1),
                    "last_runtime_event": latest.last_type if latest else None,
                    "last_runtime_event_at": latest.last_event if latest else None,
                    "last_usage_at": max((j.last_usage for j in observed), default=None),
                    "output_tokens_5m": sum(
                        n for j in observed for at, n in j.usage if at >= now - 300
                    ),
                    "output_tokens_previous_5m": sum(
                        n for j in observed for at, n in j.usage if now - 600 <= at < now - 300
                    ),
                    "pending_tools": pending,
                    "active_jobs": active_jobs,
                    "observed_seconds": round(now - started, 1),
                }
            )
    peers = [r["output_tokens_5m"] for r in rows if r["observed_seconds"] >= 300]
    median = statistics.median(peers) if peers else 0
    for row in rows:
        if (
            row["status"] == "active"
            and row["observed_seconds"] >= 300
            and median > 0
            and row["output_tokens_5m"] < median / 4
        ):
            row["status"] = "low_output"
    return {
        "observed_at": datetime.now(UTC).isoformat(),
        "active_runs": active_runs,
        "researchers": rows,
        "note": "Quiet >=180s; suspected stall >=600s without pending tools/jobs. "
        "Low output <25% of peer median over 5m. These are alerts, not proven API failures; "
        "in-flight model output may not yet be recorded. No recovery or budget changes.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=30)
    args = parser.parse_args()
    if args.interval < 5:
        parser.error("Sampling interval must be at least 5 seconds")
    batch = read(args.batch)
    output = args.batch.parent / "progress-probe"
    output.mkdir(exist_ok=True)
    journals: dict[Path, Journal] = {}
    while True:
        result = probe(batch, journals)
        temporary = output / "latest.tmp"
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(output / "latest.json")
        with (output / "observations.jsonl").open("a") as stream:
            stream.write(json.dumps(result, ensure_ascii=False) + "\n")
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if not args.watch or not result["active_runs"]:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
