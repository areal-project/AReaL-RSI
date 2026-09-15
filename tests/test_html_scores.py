"""HTML rendering, accurate score series, and browser handoff without live UI."""

import json
import webbrowser
from pathlib import Path
from unittest.mock import patch

from areal_rsi.api.base import (
    BenchmarkSplit,
    EvaluationReport,
    EvolutionTarget,
    ExperimentPlan,
    ExperimentRun,
    RunState,
)
from areal_rsi.base.storage import write_json
from areal_rsi.ui.chart_legend import legend
from areal_rsi.ui.html_scores import chart, create_html_report


def seed(root: Path, score: float = 0.5) -> None:
    write_json(
        root / "history/main/run-states/run_test.json",
        ExperimentRun(
            "run_test",
            ExperimentPlan(
                '<script>alert("x")</script>',
                "harness-evolution",
                "tb21",
                "base",
                EvolutionTarget.HARNESS,
                3,
            ),
            RunState.RUNNING,
            "base",
        ),
    )
    write_json(
        root / "history/main/runs/run_test/baseline/validate/evaluation.json",
        {
            "report": EvaluationReport("base", BenchmarkSplit.VALIDATE, False, score, {}, "today"),
        },
    )


def test_offline_report_escapes_data_and_refreshes_same_file(tmp_path: Path) -> None:
    seed(tmp_path)
    result = json.loads(create_html_report(tmp_path, scope="all", query="tb", open_browser=False))
    path = Path(result["report_path"])
    document = path.read_text()
    assert result["format"] == "html"
    assert result["report_url"] == path.as_uri()
    assert not result["browser_open_requested"]
    assert "<svg " in document
    assert "&lt;script&gt;" in document and "<script>" not in document
    assert "50.00%" in document
    assert "<script src=" not in document and "<link " not in document
    assert "░" not in document and "█" not in document
    seed(tmp_path, 0.75)
    refreshed = json.loads(
        create_html_report(tmp_path, scope="all", query="tb", open_browser=False)
    )
    assert refreshed["report_path"] == str(path)
    assert "75.00%" in path.read_text()


def test_browser_open_failure_keeps_report(tmp_path: Path) -> None:
    seed(tmp_path)
    with patch(
        "areal_rsi.ui.html_scores.webbrowser.open", side_effect=webbrowser.Error("headless")
    ):
        result = json.loads(create_html_report(tmp_path))
    assert not result["browser_open_requested"]
    assert Path(result["report_path"]).exists()
    with patch("areal_rsi.ui.html_scores.webbrowser.open", return_value=True) as opener:
        result = json.loads(create_html_report(tmp_path))
    opener.assert_called_once_with(result["report_url"], new=2)
    assert result["browser_open_requested"]


def test_failed_evaluation_breaks_score_line() -> None:
    output = chart(
        [
            {
                "name": "candidate",
                "points": [
                    {"x": 0, "y": 0.5, "label": "base"},
                    {"x": 1, "y": 0.7, "label": "one"},
                    {"x": 2, "y": None, "label": "failed"},
                    {"x": 3, "y": 0.4, "label": "three"},
                ],
            }
        ],
        0.5,
    )
    assert output.count('class="score-line"') == 1
    assert output.split("</svg>", 1)[0].count("<circle ") == 3
    assert 'class="best"' in output
    assert output.count('aria-label="图例"') == 1
    assert "candidate：实测分数（圆点为单次评测）" in output
    assert "基线：优化前得分 50.00%" in output
    assert "历史最高分：截至该轮已观察到的最高分" in output
    samples = output.split('class="chart-legend"', 1)[1]
    assert 'stroke="#8a9691"' in samples and 'stroke-dasharray="8 6"' in samples
    assert 'stroke="#c59835"' in samples and 'stroke-dasharray="4 5"' in samples
    assert "nan" not in output.lower()


def test_campaign_full_history_and_independent_validation(tmp_path: Path) -> None:
    folder = tmp_path / "codex-tb21"
    write_json(
        folder / "experiments.json",
        {
            "baseline_job": "base",
            "benchmark_ref": {"key": "tb21-full"},
            "rounds": {str(i): {} for i in range(1, 36)},
        },
    )
    write_json(
        folder / "progress.json",
        {
            "baseline": {"status": "DONE", "score_sum": 73},
            "rounds": {
                "1": {"status": "DONE", "score_sum": 81},
                "35": {"status": "RUNNING", "score_sum": 12},
            },
        },
    )
    write_json(
        folder / "validation-state.json",
        {
            "attempts": [{"round": 1, "validation": {"status": "DONE", "score_sum": 76}}],
        },
    )
    result = json.loads(create_html_report(tmp_path, scope="all", open_browser=False))
    document = Path(result["report_path"]).read_text()
    assert document.count('role="img"') == 2
    assert "91.01%" in document and "85.39%" in document
    assert document.index("91.01%") < document.index("<h3>独立复验</h3>") < document.index("85.39%")
    assert "轮次 35</td><td>—</td>" in document
    assert "候选轮次 1" in document
    assert result["warnings"] == []


def test_legend_cross_is_explicit_and_labels_are_escaped() -> None:
    output = legend([("执行无效 <TRAIN>", "#d72d46", "cross", "")])
    assert 'aria-label="图例"' in output
    assert 'stroke="#d72d46"' in output
    assert 'M 19 5 L 29 15 M 19 15 L 29 5' in output
    assert '执行无效 &lt;TRAIN&gt;' in output
