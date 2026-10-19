"""Selection rules over verified evidence, independent of task execution."""

from areal_rsi.api.records import EvaluationResult
from areal_rsi.api.research import CandidateRecord, CandidateStatus, ComparisonContract


def metric(contract: ComparisonContract, result: EvaluationResult) -> float:
    return (
        result.report.score
        if contract.metric == "score"
        else result.report.metrics[contract.metric]
    )


def improvement(contract: ComparisonContract, baseline: float, candidate: float) -> float:
    return candidate - baseline if contract.direction == "maximize" else baseline - candidate


def select_candidate(
    contract: ComparisonContract, candidates: list[CandidateRecord], candidate_id: str
) -> CandidateRecord:
    completed = [
        item
        for item in candidates
        if item.candidate_id != "base"
        and item.status == CandidateStatus.COMPLETED
        and item.validation is not None
    ]
    if len(completed) < contract.min_rounds:
        raise ValueError("Complete the minimum number of research rounds")
    baseline = next(item for item in candidates if item.candidate_id == "base")
    selected = next((item for item in completed if item.candidate_id == candidate_id), None)
    if selected is None or selected.validation is None or baseline.validation is None:
        raise ValueError("Selection requires completed validation evidence")
    baseline_score = metric(contract, baseline.validation)
    gains = [
        improvement(contract, baseline_score, metric(contract, item.validation))
        for item in completed
        if item.validation is not None
    ]
    gain = improvement(contract, baseline_score, metric(contract, selected.validation))
    if gain < max(gains):
        raise ValueError("Select a candidate with the best validation score")
    if gain <= contract.min_improvement:
        raise ValueError("Continue research: no required validation improvement yet")
    return selected
