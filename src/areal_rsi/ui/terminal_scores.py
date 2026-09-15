"""Bounded plain-text visualizations for the conversational TUI."""

from __future__ import annotations

import math
import unicodedata
from pathlib import Path
from typing import Any

from areal_rsi.ui.visualization_catalog import discover, matches, read_object
from areal_rsi.ui.web import RunReader


def _label(value: object) -> str:
    # Records are untrusted text; never emit terminal controls or Markdown fences.
    return "".join(
        char
        for char in str(value)
        if not unicodedata.category(char).startswith("C") and char != "`"
    )[:128]


def _render_run(run: dict[str, Any]) -> str:
    rows = run["score_history"]
    lines = [
        f"实验：{_label(run['run_id'])} · {_label(run['benchmark'])} · VALIDATE",
        "查询时快照；每条为一个 Task Agent 版本，按评测完成顺序排列。",
        f"记录状态：{_label(run['state'])}；"
        + ("本地执行锁活跃。" if run["worker_alive"] else "未检测到本地执行锁，不代表远端已停止。"),
    ]
    baseline = run.get("baseline_progress", {})
    if not rows and baseline.get("task_count"):
        lines.append(f"基线评测进度：{baseline.get('task') or 0}/{baseline['task_count']} 题。")
    for agent in run["agents"]:
        lines.append(
            f"{_label(agent['agent_id'])}：{_label(agent['phase'])}，"
            f"已完成 {agent['completed_iterations']}/{agent['max_iterations']} 轮；"
            f"当前评测 {agent.get('task') or 0}/{agent.get('task_count') or '?'} 题"
        )
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


def _campaign(root: Path) -> str:
    """Keep full-suite discovery and independent validation in separate series."""
    register = read_object(root / "experiments.json")
    progress = read_object(root / "progress.json") if (root / "progress.json").exists() else {}
    validation = (
        read_object(root / "validation-state.json")
        if (root / "validation-state.json").exists()
        else {}
    )
    lines = [
        f"实验：{_label(root.name)} · 独立 TB2.1 全量评测",
        f"记录更新时间：{_label(progress.get('observed_at', '未记录'))}；"
        f"复验阶段：{_label(validation.get('phase', '未开始'))}。",
        "独立调度记录快照；未实时查询 Arena，不据此确认 worker 存活。",
    ]

    def point(label: str, result: dict[str, Any]) -> str:
        status = result.get("status", "未开始")
        if status != "DONE":
            return f"{label}: {_label(status)}（无完整跑分）"
        mean = result.get("mean_over_89")
        if mean is None and isinstance(result.get("score_sum"), (int, float)):
            mean = result["score_sum"] / 89
        if not isinstance(mean, (int, float)) or not math.isfinite(mean) or not 0 <= mean <= 1:
            return f"{label}: 缺少有效跑分"
        counts = result.get("task_counts", result.get("counts", {}))
        failures = counts.get("failed")
        if failures is None:
            failures = sum(
                v
                for k, v in counts.items()
                if k
                in {
                    "FAILED",
                    "HARNESS_FAILED",
                    "SETUP_FAILED",
                    "TIMEOUT",
                    "CANCELLED",
                    "STOPPED",
                }
            )
            if not counts and isinstance(result.get("done"), int):
                failures = 89 - result["done"]
        width = round(mean * 24)
        return (
            f"{label}: {'█' * width}{'░' * (24 - width)} {mean:.2%} "
            f"({mean * 89:.0f}/89) · 非成功执行 {failures}"
        )

    lines += ["```text", point("基线", progress.get("baseline", {})), "发现阶段（全量 89 题）："]
    rounds = register.get("rounds", {})
    for number in sorted(rounds, key=int)[-30:]:
        path = root / "results" / f"round-{int(number):02d}.json"
        result = read_object(path) if path.exists() else progress.get("rounds", {}).get(number, {})
        lines.append(point(f"轮次 {number}", result))
    if len(rounds) > 30:
        lines.append(f"仅显示最近 30 / {len(rounds)} 轮。")
    lines.append("独立复验（全量 89 题，不能用发现阶段最高分代替）：")
    for attempt in validation.get("attempts", [])[-10:]:
        lines.append(
            point(f"候选轮次 {_label(attempt.get('round'))}", attempt.get("validation", {}))
        )
    if not validation.get("attempts"):
        lines.append("尚无复验记录。")
    lines += ["```", "保留原始全量跑分；非成功执行可能包含在其分母中，未改写历史评分。"]
    return "\n".join(lines)


def visualize_scores(
    root: Path,
    run_id: str | None = None,
    *,
    query: str = "",
    scope: str = "recent",
    limit: int = 4,
    offset: int = 0,
) -> str:
    if scope not in {"recent", "all", "running"} or not 1 <= limit <= 6 or offset < 0:
        raise ValueError("Invalid visualization selection")
    entries, warnings = discover(root)
    if run_id:
        entries = [entry for entry in entries if entry["run_id"] == run_id]
        if not entries:
            raise FileNotFoundError(run_id)
    if query:
        entries = [entry for entry in entries if matches(entry, query)]
    if scope in {"running", "all"}:
        for entry in entries:
            entry["active"] = entry["kind"] == "rsi" and RunReader(entry["root"]).worker_alive(
                entry["run_id"]
            )
        if scope == "running":
            entries = [entry for entry in entries if entry["active"]]
        else:
            entries.sort(key=lambda entry: (entry["active"], entry["started_at"]), reverse=True)
    if not entries:
        message = (
            "当前工作区没有实验记录。" if not query and scope == "recent" else "没有匹配的实验。"
        )
        return message + ("\n" + "\n".join(_label(w) for w in warnings[:5]) if warnings else "")
    if run_id and len(entries) > 1:
        return "实验 ID 存在于多个目录，请用 query 指定目录区分：\n" + "\n".join(
            _label(entry["root"]) for entry in entries[:6]
        )
    count = 1 if scope == "recent" else limit
    selected = entries[offset : offset + count]
    sections = [
        f"找到 {len(entries)} 个实验，本页显示 {len(selected)} 个；"
        f"来源工作区 {_label(root.resolve())}。",
        "各实验独立展示；VALIDATE 分区与全量 89 题、发现阶段与复验不合并排名。",
    ]
    for entry in selected:
        sections.append(f"\n来源：{_label(entry['root'])}")
        try:
            sections.append(
                _render_run(RunReader(entry["root"]).detail(entry["run_id"]))
                if entry["kind"] == "rsi"
                else _campaign(entry["root"])
            )
        except (OSError, ValueError, KeyError, TypeError):
            sections.append(
                f"实验 {_label(entry['run_id'])} 记录不完整或格式不兼容；已跳过，其他图表继续展示。"
            )
    if offset + len(selected) < len(entries):
        sections.append(
            f"还有实验未显示；保持筛选条件，传 offset={offset + len(selected)} 查看下一页。"
        )
    if warnings:
        sections.extend(_label(warning) for warning in warnings[:5])
    return "\n\n".join(sections)
