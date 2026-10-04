from __future__ import annotations

from areal_rsi.api.records import RsiCaseScore, RsiEvaluationReport, RsiSplit
from areal_rsi.api.research import HarnessSelectionRule
from areal_rsi.infra.coordination.policies.harness import (
    assess_harness_noise,
    select_harness,
)


def _report(harness_id: str, seed: int, score: float) -> RsiEvaluationReport:
    return RsiEvaluationReport(
        "sha256:" + "1" * 64,
        harness_id,
        "taskset_t0",
        RsiSplit.HARNESS_SELECT,
        "sha256:" + "2" * 64,
        "sha256:" + "3" * 64,
        seed,
        1.0,
        64,
        (RsiCaseScore("case1", score, "response", 10),),
        f"/tmp/{harness_id}_{seed}.trajectories.json",
        "sha256:" + "4" * 64,
    )


def test_aa_spread_prevents_selection_on_noisy_gain() -> None:
    baseline = _report("harness_h0", 1, 0.0)
    repeat = _report("harness_h0", 2, 1.0)
    candidate = _report("harness_h1", 1, 1.0)
    noise = assess_harness_noise(baseline, repeat, 0.05)
    assert noise.observed_score_spread == noise.effective_noise_band == 1.0
    decision = select_harness(
        baseline, candidate, HarnessSelectionRule(noise.effective_noise_band, 0.2)
    )
    assert not decision.accepted
    assert decision.selected_harness_id == "harness_h0"
