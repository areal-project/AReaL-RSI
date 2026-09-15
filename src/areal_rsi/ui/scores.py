"""Read durable validation checkpoints, including evaluations awaiting selection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from areal_rsi.api.base import ExperimentRun
from areal_rsi.base.runs import FileRunStore
from areal_rsi.base.storage import encode, safe_name
from areal_rsi.workflows.records import evolution_history


def score_history(folder: Path, run: ExperimentRun, store: FileRunStore) -> list[dict[str, Any]]:
    if run.plan.workflow not in {"harness-evolution", "artifact-evolution"}:
        return []
    rows: list[dict[str, Any]] = []

    def append(
        path: Path,
        agent_id: str,
        iteration: int,
        parent: str | None,
        selection: str,
        fallback: dict[str, Any] | None = None,
    ) -> None:
        report = json.loads(path.read_text())["report"] if path.exists() else fallback
        if report is None:
            return
        rows.append(
            {
                "agent_id": agent_id,
                "iteration": iteration,
                "solution_id": report["solution_id"],
                "input_solution_id": parent,
                "score": report["score"],
                "evaluated_at": report["created_at"],
                "selection": selection,
            }
        )

    append(folder / "baseline/validate/evaluation.json", "baseline", 0, None, "baseline")
    for agent in run.research_agents:
        history = evolution_history(store, agent)
        for step in history:
            selection = (
                "stopped"
                if step.next_solution_id is None
                else "selected"
                if step.next_solution_id == step.candidate_solution_id
                else "reverted"
            )
            append(
                folder
                / safe_name(agent.agent_id)
                / f"iteration-{step.iteration}"
                / "validate/evaluation.json",
                agent.agent_id,
                step.iteration,
                step.input_solution_id,
                selection,
                encode(step.validation_report),
            )
        # The workflow writes evaluation.json before calling the selection agent.
        iteration = len(history) + 1
        pending = folder / safe_name(agent.agent_id) / f"iteration-{iteration}"
        candidate_path = pending / "candidate.json"
        parent = (
            json.loads(candidate_path.read_text()).get("parent_solution_id")
            if candidate_path.exists()
            else None
        )
        append(pending / "validate/evaluation.json", agent.agent_id, iteration, parent, "pending")

    rows.sort(key=lambda row: (row["evaluated_at"], row["agent_id"], row["iteration"]))
    scores = {row["solution_id"]: row["score"] for row in rows}
    baseline = scores.get(run.plan.baseline_solution_id)
    best = None
    for row in rows:
        best = row["score"] if best is None else max(best, row["score"])
        parent_score = scores.get(row["input_solution_id"])
        row["best_score"] = best
        row["baseline_delta"] = row["score"] - baseline if baseline is not None else None
        row["parent_delta"] = row["score"] - parent_score if parent_score is not None else None
    return rows
