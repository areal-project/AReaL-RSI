"""Scores survive selection failures and retries without duplicate observations."""

from pathlib import Path

from areal_rsi.api.base import (
    BenchmarkSplit,
    ContentRef,
    EvaluationReport,
    EvolutionTarget,
    ExperimentPlan,
    ExperimentRun,
    HarnessChange,
    ResearchAgentState,
    ResearchIteration,
    RunState,
)
from areal_rsi.base.storage import write_json
from areal_rsi.ui.scores import score_history
from areal_rsi.ui.terminal_scores import visualize_scores


def test_scores_include_pending_validation_and_actual_parent(tmp_path: Path) -> None:
    plan = ExperimentPlan("test", "harness-evolution", "test", "base", EvolutionTarget.HARNESS, 3)
    agent = ResearchAgentState("research-1", RunState.RUNNING)
    run = ExperimentRun("run", plan, RunState.RUNNING, "base", (agent,))

    def report(version: str, score: float, time: str) -> EvaluationReport:
        return EvaluationReport(version, BenchmarkSplit.VALIDATE, False, score, {}, time)

    baseline = report("base", 0.5, "2026-09-15T00:00:00Z")
    write_json(tmp_path / "baseline/validate/evaluation.json", {"report": baseline})
    candidate = report("v1", 0.3, "2026-09-15T00:01:00Z")
    folder = tmp_path / "research-1/iteration-1"
    write_json(folder / "candidate.json", {"parent_solution_id": "base"})
    write_json(folder / "validate/evaluation.json", {"report": candidate, "evidence": "private"})
    rows = score_history(tmp_path, run)
    assert len(rows) == 2
    assert rows[-1]["selection"] == "pending"
    assert round(rows[-1]["parent_delta"], 2) == -0.2
    assert rows[-1]["best_score"] == 0.5
    assert "evidence" not in rows[-1]

    agent.iterations = (
        ResearchIteration(
            1,
            "base",
            baseline,
            ContentRef("content://test", "test"),
            HarnessChange(ContentRef("content://change", "change"), "reason"),
            "v1",
            candidate,
            "base",
            baseline.created_at,
            candidate.created_at,
        ),
    )
    # A rollback means v2 must be compared to base, not the preceding candidate.
    folder = tmp_path / "research-1/iteration-2"
    write_json(folder / "candidate.json", {"parent_solution_id": "base"})
    write_json(
        folder / "validate/evaluation.json",
        {
            "report": report("v2", 0.6, "2026-09-15T00:02:00Z"),
        },
    )
    rows = score_history(tmp_path, run)
    assert len(rows) == 3
    assert rows[1]["selection"] == "reverted"
    assert round(rows[-1]["parent_delta"], 2) == 0.1
    assert rows[-1]["best_score"] == 0.6
    assert score_history(tmp_path, run) == rows


def test_no_score_for_incomplete_or_failed_evaluation(tmp_path: Path) -> None:
    plan = ExperimentPlan("test", "harness-evolution", "test", "base", EvolutionTarget.HARNESS, 1)
    run = ExperimentRun("run", plan, RunState.FAILED, "base")
    assert score_history(tmp_path, run) == []


def test_terminal_visualization_reads_latest_run_and_escapes_controls(tmp_path: Path) -> None:
    assert "没有实验记录" in visualize_scores(tmp_path)
    plan = ExperimentPlan(
        "test",
        "harness-evolution",
        "bench\x1b\n```",
        "base",
        EvolutionTarget.HARNESS,
        1,
    )
    run = ExperimentRun("run", plan, RunState.RUNNING, "base")
    write_json(tmp_path / "run-states/run.json", run)
    assert "尚无完整评测" in visualize_scores(tmp_path)
    write_json(
        tmp_path / "runs/run/baseline/validate/evaluation.json",
        {
            "report": EvaluationReport(
                "base",
                BenchmarkSplit.VALIDATE,
                False,
                0.5,
                {},
                "2026-09-15T00:00:00Z",
            ),
        },
    )
    text = visualize_scores(tmp_path)
    assert "████████████░░░░░░░░░░░░" in text
    assert "历史最高 50.0%" in text
    assert "版本 base" in text
    assert "\x1b" not in text
    assert text.count("```") == 2
    assert text == visualize_scores(tmp_path, "run")
