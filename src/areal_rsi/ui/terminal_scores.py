"""Bounded plain-text visualizations for the conversational TUI."""

from __future__ import annotations

import unicodedata
from pathlib import Path

from areal_rsi.ui.web import RunReader


def _label(value: object) -> str:
    # Records are untrusted text; never emit terminal controls or Markdown fences.
    return "".join(
        char
        for char in str(value)
        if not unicodedata.category(char).startswith("C") and char != "`"
    )[:128]


def visualize_scores(root: Path, run_id: str | None = None) -> str:
    reader = RunReader(root)
    if run_id is None:
        runs = reader.list()
        if not runs:
            return "当前工作区没有实验记录。"
        run_id = runs[0]["run_id"]
    run = reader.detail(run_id)
    rows = run["score_history"]
    lines = [
        f"实验：{_label(run_id)} · {_label(run['benchmark'])} · VALIDATE",
        "查询时快照；每条为一个 Task Agent 版本，按评测完成顺序排列。",
    ]
    if not rows:
        return "\n".join([*lines, "尚无完整评测结果；运行中或失败的评测不记作零分。"])
    low = min(0.0, *(row["score"] for row in rows))
    high = max(1.0, *(row["score"] for row in rows))
    lines += [
        f"已完成 {len(rows)} 次评测（含基线）；历史最高 {rows[-1]['best_score']:.1%}。",
        f"条形图范围 {low:.1%} → {high:.1%}；Δ 为相对实际父版本的变化（百分点）。",
        "```text",
    ]
    if len(rows) > 30:
        lines.append(f"仅展示最近 30 / {len(rows)} 条，历史最高分仍按全部记录计算。")
    labels = {
        "baseline": "基线",
        "selected": "继续采用",
        "reverted": "选择其他版本",
        "stopped": "研究线结束",
        "pending": "待完成版本选择",
    }
    for row in rows[-30:]:
        width = round((row["score"] - low) / (high - low) * 24)
        bar = "█" * width + "░" * (24 - width)
        delta = row["parent_delta"]
        change = "—" if delta is None else f"{delta * 100:+.1f}"
        lines.append(
            f"{_label(row['agent_id'])} / {row['iteration']}  "
            f"{bar} {row['score']:7.1%}  Δ {change}  {labels[row['selection']]}"
        )
        lines.append(f"  版本 {_label(row['solution_id'])}")
    lines += ["```", "这是配置的 VALIDATE 分区跑分；说“刷新跑分”可获取最新快照。"]
    return "\n".join(lines)
