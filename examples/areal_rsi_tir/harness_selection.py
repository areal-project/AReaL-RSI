"""Select frozen Harness candidates with code-enforced quality and cost gates."""

from __future__ import annotations

import hashlib

from areal_rsi.api.control import json_bytes
from areal_rsi.api.records import RsiEvaluationReport, RsiSplit
from areal_rsi.api.research import (
    HarnessNoiseEvidence,
    HarnessSelectionDecision,
    HarnessSelectionRule,
)


def assess_harness_noise(
    primary: RsiEvaluationReport,
    repeat: RsiEvaluationReport,
    minimum_band: float,
) -> HarnessNoiseEvidence:
    if primary.split is not RsiSplit.HARNESS_SELECT or repeat.split is not primary.split:
        raise ValueError("Harness A/A requires the frozen harness_select split")
    for field in (
        "model_digest",
        "harness_id",
        "task_set_id",
        "runner_digest",
        "verifier_digest",
        "temperature",
        "max_new_tokens",
    ):
        if getattr(primary, field) != getattr(repeat, field):
            raise ValueError(f"Harness A/A changed {field}")
    if primary.seed == repeat.seed or tuple(case.task_id for case in primary.cases) != tuple(
        case.task_id for case in repeat.cases
    ):
        raise ValueError("Harness A/A needs the same tasks and distinct seeds")
    spread = abs(primary.score - repeat.score)
    rule = HarnessSelectionRule(minimum_band, 0)
    return HarnessNoiseEvidence(
        "sha256:" + hashlib.sha256(json_bytes(primary)).hexdigest(),
        "sha256:" + hashlib.sha256(json_bytes(repeat)).hexdigest(),
        primary.seed,
        repeat.seed,
        spread,
        max(rule.noise_band, spread),
    )


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
