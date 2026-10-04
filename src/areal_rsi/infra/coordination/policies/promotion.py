"""Preselect a model/Harness pair on dev; use sealed test only as a fixed gate."""

from __future__ import annotations

import random
from collections.abc import Sequence

from areal_rsi.api.records import RsiEvaluationReport, RsiSplit
from areal_rsi.api.research import (
    RsiPromotionDecision,
    RsiPromotionProposal,
    RsiPromotionRule,
)


def _matrix(
    reports: Sequence[RsiEvaluationReport],
    *,
    split: RsiSplit,
    baseline_model_digest: str,
    trained_model_digest: str,
    baseline_harness_id: str,
    candidate_harness_id: str,
) -> dict[tuple[str, str], RsiEvaluationReport]:
    if len(reports) != 4 or any(report.split is not split for report in reports):
        raise ValueError("RSI promotion requires exactly four reports on the expected split")
    reference = reports[0]
    for report in reports[1:]:
        for field in (
            "task_set_id",
            "runner_digest",
            "verifier_digest",
            "seed",
            "temperature",
            "max_new_tokens",
        ):
            if getattr(report, field) != getattr(reference, field):
                raise ValueError(f"RSI cross-evaluation changed {field}")
        if tuple(case.task_id for case in report.cases) != tuple(
            case.task_id for case in reference.cases
        ):
            raise ValueError("RSI cross-evaluation changed task identities")
    expected = {
        (baseline_model_digest, baseline_harness_id),
        (baseline_model_digest, candidate_harness_id),
        (trained_model_digest, baseline_harness_id),
        (trained_model_digest, candidate_harness_id),
    }
    matrix = {(report.model_digest, report.harness_id): report for report in reports}
    if (
        baseline_model_digest == trained_model_digest
        or baseline_harness_id == candidate_harness_id
        or set(matrix) != expected
        or len(matrix) != 4
    ):
        raise ValueError("RSI cross-evaluation does not contain the exact 2x2 version matrix")
    return matrix


def _relative_tokens(candidate: RsiEvaluationReport, baseline: RsiEvaluationReport) -> float:
    if baseline.generated_tokens < 1:
        raise ValueError("RSI baseline has no generated tokens")
    return (candidate.generated_tokens - baseline.generated_tokens) / baseline.generated_tokens


def _paired_lower_bound(candidate: RsiEvaluationReport, baseline: RsiEvaluationReport) -> float:
    """Fixed-seed 95% paired bootstrap lower bound over frozen task identities."""

    differences = [
        newer.score - older.score
        for newer, older in zip(candidate.cases, baseline.cases, strict=True)
    ]
    rng = random.Random(0)
    count = len(differences)
    means = sorted(
        sum(differences[rng.randrange(count)] for _ in range(count)) / count for _ in range(2000)
    )
    return means[int(0.025 * len(means))]


def propose_promotion(
    reports: Sequence[RsiEvaluationReport],
    *,
    baseline_model_digest: str,
    trained_model_digest: str,
    baseline_harness_id: str,
    candidate_harness_id: str,
    rule: RsiPromotionRule,
) -> RsiPromotionProposal | None:
    """Choose on model_dev once; None means no candidate merits sealed evaluation."""

    matrix = _matrix(
        reports,
        split=RsiSplit.MODEL_DEV,
        baseline_model_digest=baseline_model_digest,
        trained_model_digest=trained_model_digest,
        baseline_harness_id=baseline_harness_id,
        candidate_harness_id=candidate_harness_id,
    )
    baseline = matrix[(baseline_model_digest, baseline_harness_id)]
    internalized = matrix[(trained_model_digest, baseline_harness_id)]
    if (
        rule.objective == "model_internalization"
        and internalized.score - baseline.score <= rule.minimum_score_gain
    ):
        return None
    alternatives = [
        report
        for key, report in matrix.items()
        if key != (baseline_model_digest, baseline_harness_id)
        and (rule.objective != "model_internalization" or key[0] == trained_model_digest)
        and report.score - baseline.score > rule.minimum_score_gain
        and _relative_tokens(report, baseline) <= rule.max_relative_token_cost
    ]
    if not alternatives:
        return None
    chosen = max(
        alternatives,
        key=lambda report: (
            report.score,
            -report.generated_tokens,
            report.model_digest,
            report.harness_id,
        ),
    )
    return RsiPromotionProposal(
        baseline_model_digest,
        baseline_harness_id,
        chosen.model_digest,
        chosen.harness_id,
        baseline.task_set_id,
        rule,
        chosen.score - baseline.score,
        _relative_tokens(chosen, baseline),
    )


def confirm_promotion(
    proposal: RsiPromotionProposal,
    reports: Sequence[RsiEvaluationReport],
    *,
    trained_model_digest: str,
    candidate_harness_id: str,
) -> RsiPromotionDecision:
    """Never choose another candidate after observing sealed_test results."""

    matrix = _matrix(
        reports,
        split=RsiSplit.SEALED_TEST,
        baseline_model_digest=proposal.baseline_model_digest,
        trained_model_digest=trained_model_digest,
        baseline_harness_id=proposal.baseline_harness_id,
        candidate_harness_id=candidate_harness_id,
    )
    baseline = matrix[(proposal.baseline_model_digest, proposal.baseline_harness_id)]
    if baseline.task_set_id != proposal.task_set_id:
        raise ValueError("Sealed evaluation belongs to a different task set")
    selected = matrix.get((proposal.candidate_model_digest, proposal.candidate_harness_id))
    if selected is None:
        raise ValueError("Sealed evaluation lacks the preselected candidate")
    gain = selected.score - baseline.score
    cost = _relative_tokens(selected, baseline)
    lower = _paired_lower_bound(selected, baseline)
    internalized = matrix[(trained_model_digest, proposal.baseline_harness_id)]
    if len(selected.cases) < proposal.rule.minimum_sealed_cases:
        accepted, reason = False, "sealed task count is below the preregistered minimum"
    elif (
        proposal.rule.objective == "model_internalization"
        and internalized.score - baseline.score <= proposal.rule.minimum_score_gain
    ):
        accepted, reason = False, "trained model did not transfer under baseline Harness"
    elif (
        proposal.rule.objective == "model_internalization"
        and _paired_lower_bound(internalized, baseline) <= 0
    ):
        accepted, reason = False, "model transfer is uncertain on paired sealed tasks"
    elif gain <= proposal.rule.minimum_score_gain:
        accepted, reason = False, "sealed task gain did not meet the preregistered threshold"
    elif lower <= 0:
        accepted, reason = False, "sealed task gain is uncertain under paired resampling"
    elif cost > proposal.rule.max_relative_token_cost:
        accepted, reason = False, "sealed token cost exceeded the preregistered limit"
    else:
        accepted, reason = True, "preselected version pair passed the sealed release gate"
    return RsiPromotionDecision(proposal, accepted, gain, cost, lower, reason)
