"""Select frozen Harness candidates with code-enforced quality and cost gates."""

from __future__ import annotations

import hashlib
from typing import Literal

from areal_rsi.api.control import json_bytes
from areal_rsi.api.records import AgentEvaluationReport, RsiEvaluationReport, RsiSplit
from areal_rsi.api.research import (
    AgentHarnessChoice,
    AgentHarnessSearchDecision,
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


def select_agent_harnesses(
    baseline: AgentEvaluationReport,
    repeat: AgentEvaluationReport,
    candidates: tuple[AgentEvaluationReport, ...],
    *,
    minimum_score_gain: float = 0.02,
    max_relative_token_cost: float = 0.2,
    on_no_improvement: Literal["reuse", "stop"] = "reuse",
) -> AgentHarnessSearchDecision:
    """Choose the best passing Claude Code Harness on one frozen selection split."""
    if on_no_improvement not in {"reuse", "stop"}:
        raise ValueError("Agent search needs a valid fallback policy")
    if minimum_score_gain < 0 or max_relative_token_cost < 0:
        raise ValueError("Agent search gates must be nonnegative")
    if baseline.split is not RsiSplit.HARNESS_SELECT:
        raise ValueError("Agent search requires harness_select reports")
    identity = (
        "task_set_id",
        "model",
        "executable_digest",
        "backend_model_digest",
        "verifier_digest",
    )
    task_ids = tuple(case.task_id for case in baseline.cases)
    for report in (repeat, *candidates):
        if (
            report.split is not baseline.split
            or any(getattr(report, field) != getattr(baseline, field) for field in identity)
            or tuple(case.task_id for case in report.cases) != task_ids
        ):
            raise ValueError("Agent search reports changed frozen evaluation inputs")
    if repeat.harness_id != baseline.harness_id or repeat.trial_id == baseline.trial_id:
        raise ValueError("Agent A/A requires two distinct trials of the same Harness")
    if len({report.harness_id for report in candidates}) != len(candidates) or any(
        report.harness_id == baseline.harness_id for report in candidates
    ):
        raise ValueError("Agent candidates must be distinct from each other and H0")
    baseline_tokens = sum(case.output_tokens for case in baseline.cases)
    if baseline_tokens < 1:
        raise ValueError("Agent baseline has no output tokens")
    noise = max(minimum_score_gain, abs(baseline.score - repeat.score))
    choices = []
    for report in candidates:
        gain = report.score - baseline.score
        cost = (
            sum(case.output_tokens for case in report.cases) - baseline_tokens
        ) / baseline_tokens
        if gain <= noise:
            reason = "gain does not exceed A/A noise and the minimum margin"
        elif cost > max_relative_token_cost:
            reason = "output token cost exceeds the limit"
        else:
            reason = "candidate passed score and cost gates"
        choices.append(
            AgentHarnessChoice(
                report.harness_id,
                sum(case.score for case in report.cases),
                gain,
                cost,
                reason == "candidate passed score and cost gates",
                reason,
                report,
            )
        )
    accepted = [choice for choice in choices if choice.accepted]
    winner = (
        max(
            accepted,
            key=lambda item: (item.correct, -item.relative_token_cost, item.candidate_harness_id),
        )
        if accepted
        else None
    )
    return AgentHarnessSearchDecision(
        baseline,
        repeat,
        tuple(choices),
        winner.candidate_harness_id if winner else baseline.harness_id,
        noise,
        on_no_improvement,
        winner is not None or on_no_improvement == "reuse",
    )
