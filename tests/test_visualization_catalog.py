"""Historical discovery, live selection, and independent campaign score semantics."""

import fcntl
from pathlib import Path

import pytest

from areal_rsi.api.base import (
    BenchmarkSplit,
    EvaluationReport,
    EvolutionTarget,
    ExperimentPlan,
    ExperimentRun,
    RunState,
)
from areal_rsi.base.storage import write_json
from areal_rsi.ui.terminal_scores import visualize_scores
from areal_rsi.ui.visualization_catalog import discover


def save_run(root: Path, run_id: str, date: str = "2026-09-15T00:00:00Z") -> None:
    plan = ExperimentPlan(
        "优化 TB2.1", "harness-evolution", "tb21", "base", EvolutionTarget.HARNESS, 3
    )
    write_json(
        root / "run-states" / f"{run_id}.json",
        ExperimentRun(
            run_id,
            plan,
            RunState.RUNNING,
            "base",
            started_at=date,
        ),
    )
    write_json(
        root / "runs" / run_id / "baseline/validate/evaluation.json",
        {
            "report": EvaluationReport("base", BenchmarkSplit.VALIDATE, False, 0.5, {}, date),
        },
    )


def test_nested_history_and_filter_pagination(tmp_path: Path) -> None:
    save_run(tmp_path / "areal-tb21/main", "main")
    save_run(tmp_path / "areal-tb21/extension", "extension", "2026-09-16T00:00:00Z")
    save_run(tmp_path / "datasets/fixture", "skip")
    assert len(discover(tmp_path)[0]) == 2
    assert "实验：main" in visualize_scores(tmp_path, "main")
    first = visualize_scores(tmp_path, scope="all", query="Terminal Bench", limit=1)
    assert "实验：extension" in first
    assert "offset=1" in first
    assert "实验：main" in visualize_scores(tmp_path, scope="all", query="tb", limit=1, offset=1)
    assert "没有匹配" in visualize_scores(tmp_path, query="nonexistent")
    with pytest.raises(FileNotFoundError):
        visualize_scores(tmp_path, "wrong-id")


def test_running_requires_live_lock_and_refresh_reads_new_score(tmp_path: Path) -> None:
    root = tmp_path / "historical/main"
    save_run(root, "live")
    save_run(tmp_path / "old", "stale")
    locks = root / "locks"
    locks.mkdir()
    with (locks / "live.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        text = visualize_scores(tmp_path, scope="running")
        assert "实验：live" in text
        assert "实验：stale" not in text
        assert "50.0%" in text
        write_json(
            root / "runs/live/baseline/validate/evaluation.json",
            {
                "report": EvaluationReport("base", BenchmarkSplit.VALIDATE, False, 0.75, {}, "now"),
            },
        )
        assert "75.0%" in visualize_scores(tmp_path, scope="running")
    assert "没有匹配" in visualize_scores(tmp_path, scope="running")


def test_campaign_discovery_and_validation_are_separate(tmp_path: Path) -> None:
    root = tmp_path / "codex-tb21"
    write_json(
        root / "experiments.json",
        {
            "baseline_job": "base",
            "benchmark_ref": {"key": "tb21-full"},
            "rounds": {"1": {}, "2": {}},
        },
    )
    write_json(
        root / "progress.json",
        {
            "baseline": {"status": "DONE", "score_sum": 73, "counts": {"DONE": 87, "TIMEOUT": 2}},
            "rounds": {"2": {"status": "RUNNING", "score_sum": 1}},
        },
    )
    write_json(
        root / "results/round-01.json",
        {
            "status": "DONE",
            "mean_over_89": 81 / 89,
            "done": 89,
        },
    )
    write_json(
        root / "validation-state.json",
        {
            "phase": "success",
            "attempts": [
                {"round": 1, "validation": {"status": "DONE", "mean_over_89": 76 / 89, "done": 89}},
            ],
        },
    )
    text = visualize_scores(tmp_path, scope="all")
    assert "73/89" in text
    assert "81/89" in text
    assert "76/89" in text
    assert "非成功执行 2" in text
    assert "轮次 2: RUNNING（无完整跑分）" in text
    assert text.index("81/89") < text.index("独立复验（") < text.index("76/89")


def test_corrupt_run_isolated_and_symlinks_not_followed(tmp_path: Path) -> None:
    save_run(tmp_path / "good", "good")
    write_json(tmp_path / "broken/run-states/broken.json", {"invalid": True})
    (tmp_path / "alias").symlink_to(tmp_path / "good", target_is_directory=True)
    entries, warnings = discover(tmp_path)
    assert len(entries) == 1
    assert warnings
    text = visualize_scores(tmp_path, scope="all")
    assert "实验：good" in text
    assert "无法读取实验索引" in text


def test_duplicate_run_ids_require_directory_filter(tmp_path: Path) -> None:
    save_run(tmp_path / "first", "duplicate")
    save_run(tmp_path / "second", "duplicate")
    assert "存在于多个目录" in visualize_scores(tmp_path, "duplicate")
    assert "实验：duplicate" in visualize_scores(tmp_path, "duplicate", query="first")
