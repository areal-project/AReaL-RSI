"""Select frozen Harness candidates with code-enforced quality and cost gates."""

from __future__ import annotations

from areal_rsi.api.records import RsiEvaluationReport, RsiSplit
from areal_rsi.api.research import HarnessSelectionDecision, HarnessSelectionRule


def select_harness(
    incumbent: RsiEvaluationReport,
    candidate: RsiEvaluationReport,
    rule: HarnessSelectionRule,
) -> HarnessSelectionDecision:
    if incumbent.split is not RsiSplit.HARNESS_SELECT or candidate.split is not incumbent.split:
        raise ValueError("Harness selection requires the frozen harness_select split")
    for field in (
        "model_digest",
        "task_set_id",
        "runner_digest",
        "verifier_digest",
        "seed",
        "temperature",
        "max_new_tokens",
    ):
        if getattr(incumbent, field) != getattr(candidate, field):
            raise ValueError(f"Harness comparison changed {field}")
    if tuple(case.task_id for case in incumbent.cases) != tuple(
        case.task_id for case in candidate.cases
    ):
        raise ValueError("Harness comparison changed task identities or order")
    if incumbent.generated_tokens < 1:
        raise ValueError("Harness baseline has no generated tokens")
    gain = candidate.score - incumbent.score
    cost = (candidate.generated_tokens - incumbent.generated_tokens) / incumbent.generated_tokens
    if gain <= rule.noise_band:
        accepted, reason = False, "gain does not exceed the preregistered noise band"
    elif cost > rule.max_relative_token_cost:
        accepted, reason = False, "extra generated tokens exceed the cost limit"
    else:
        accepted, reason = True, "candidate passed score and token-cost gates"
    return HarnessSelectionDecision(
        selected_harness_id=candidate.harness_id if accepted else incumbent.harness_id,
        candidate_harness_id=candidate.harness_id,
        accepted=accepted,
        score_gain=gain,
        relative_token_cost=cost,
        reason=reason,
        rule=rule,
        baseline=incumbent,
        candidate=candidate,
    )
