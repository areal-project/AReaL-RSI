"""Records specific to the existing evolution workflow, outside generic nodes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from areal_rsi.api.base import (
    ContentRef,
    EvaluationReport,
    IterationState,
    ResearchAgentState,
    ResearchChange,
    ResearchIteration,
)
from areal_rsi.base.runs import FileRunStore
from areal_rsi.base.storage import decode


@dataclass(slots=True)
class EvolutionResult:
    """Completed result for the existing Harness/Artifact workflows.

    Generic research nodes need not produce this record. Workflows that use it
    reference SolutionVersion IDs here and keep version bodies in VersionManager.
    """

    iteration: int
    input_solution_id: str
    train_report: EvaluationReport
    evidence: ContentRef
    change: ResearchChange
    candidate_solution_id: str
    validation_report: EvaluationReport
    next_solution_id: str | None
    started_at: str
    finished_at: str


def evolution_result(store: FileRunStore, node: ResearchIteration) -> EvolutionResult:
    if node.state != IterationState.COMPLETED or node.report_ref is None:
        raise ValueError("Evolution node has no completed result")
    return decode_evolution_result(store.read_json(node.report_ref))


def decode_evolution_result(value: dict[str, Any]) -> EvolutionResult:
    data = dict(value)
    # Both historical layouts remain readable without rewriting source records.
    if "trajectories" in data:
        data.setdefault("evidence", data.pop("trajectories"))
    result: EvolutionResult = decode(EvolutionResult, data)
    return result


def evolution_history(store: FileRunStore, agent: ResearchAgentState) -> list[EvolutionResult]:
    return [
        evolution_result(store, step)
        for step in agent.iterations
        if step.state == IterationState.COMPLETED
    ]
