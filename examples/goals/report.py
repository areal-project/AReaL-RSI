"""Summarize authoritative Goal checkpoints without running inference or evaluation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from areal_rsi.api.base import ExperimentRun
from areal_rsi.api.goal import GoalRevision
from areal_rsi.base.storage import FileRecords, encode, now, write_json
from areal_rsi.goals import GoalService


def report(root: Path, goal_id: str | None) -> dict[str, Any]:
    service = GoalService(root)
    goals = service.records.list(GoalRevision)
    goal = service.get(goal_id) if goal_id else max(goals, key=lambda g: g.created_at)
    status = service.status(goal.goal_id)
    run = FileRecords(root / "run-states").get(goal.run_id, ExperimentRun) if goal.run_id else None
    folder = root / "runs" / (goal.run_id or "missing")
    final = folder / "final/report.json"
    iterations = (
        [
            {"agent_id": agent.agent_id, **encode(iteration)}
            for agent in run.research_agents
            for iteration in agent.iterations
        ]
        if run
        else []
    )
    checks = []
    for path in sorted(folder.rglob("task-*.json")):
        record = json.loads(path.read_text())
        checks.append(
            {
                "checkpoint": str(path.relative_to(root)),
                "task_id": record["task"]["task_id"],
                "trajectory_id": record["result"]["trajectory"]["trajectory_id"],
                "verification": record["verification"],
            }
        )
    return {
        "schema_version": 1,
        "recorded_at": now(),
        "classification": "goal_execution_record",
        "root": str(root),
        "goal_id": goal.goal_id,
        "revision": goal.revision,
        "run_id": goal.run_id,
        "state": status["state"],
        "worker_active": status["worker_active"],
        "progress": status["progress"],
        "resource_usage": status["resource_usage"],
        "outcome": status["outcome"],
        "previous_outcome": status["previous_outcome"],
        "retry_history": status["retry_history"],
        "contract_identity": goal.frozen_identity.get("digest"),
        "researcher": encode(goal.researcher),
        "spec": encode(goal.spec),
        "completed_iteration_count": len(iterations),
        "iterations": iterations,
        "task_checkpoints": checks,
        "independent_acceptance": json.loads(final.read_text()) if final.exists() else None,
        "effect_acceptance_passed": bool(status["outcome"] and status["outcome"]["achieved"]),
        "limitations": [
            "Unit tests and development preflights do not establish real effects.",
            "Token, fee and human-time costs are unknown unless explicitly observed.",
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--goal-id")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    result = report(root, args.goal_id)
    destination = args.output or root / "demo-report.json"
    write_json(destination, result)
    print(
        json.dumps(
            {
                "report": str(destination),
                "state": result["state"],
                "iterations": result["completed_iteration_count"],
                "effect_acceptance_passed": result["effect_acceptance_passed"],
            }
        )
    )
