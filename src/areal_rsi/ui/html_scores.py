"""Standalone HTML score reports: no server, CDN, or additional evaluations."""

from __future__ import annotations

import hashlib
import html
import json
import math
import os
import tempfile
import webbrowser
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any

from areal_rsi.ui.chart_legend import legend
from areal_rsi.ui.visualization_catalog import discover, matches, read_object
from areal_rsi.ui.web import RunReader

COLORS = ("#087f8c", "#5266c6", "#b86226", "#9560a4", "#bd4965", "#528240", "#447ea2")


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def valid_score(value: Any) -> float | None:
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def chart(
    series: list[dict[str, Any]],
    baseline: float | None,
    *,
    xlabel: str = "优化轮次",
    best: bool = True,
) -> str:
    points = [point for line in series for point in line["points"]]
    valid = [point for point in points if point["y"] is not None]
    if not valid:
        return (
            '<div class="empty">尚无完整评测结果。刷新报告后可查看新分数，'
            "未完成或失败不记作零分。</div>"
        )
    width, height, left, right, top, bottom = 1040, 340, 70, 28, 28, 55
    scores = [p["y"] for p in valid] + ([baseline] if baseline is not None else [])
    low, high = min(0, *scores), max(1, *scores)
    xmax = max(1, *(p["x"] for p in points))

    def x(value: float) -> float:
        return float(left + (width - left - right) * value / xmax)

    def y(value: float) -> float:
        return float(top + (height - top - bottom) * (high - value) / (high - low))

    svg = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Benchmark 分数折线图">']
    for i in range(5):
        value = low + (high - low) * i / 4
        svg.append(
            f'<line class="grid" x1="{left}" x2="{width - right}" y1="{y(value)}" y2="{y(value)}"/>'
            f'<text x="{left - 12}" y="{y(value) + 5}" text-anchor="end">{value:.0%}</text>'
        )
    ticks = sorted({0, xmax, *range(0, int(xmax) + 1, max(1, math.ceil(xmax / 10)))})
    for tick in ticks:
        svg.append(f'<text x="{x(tick)}" y="{height - 28}" text-anchor="middle">{tick}</text>')
    svg.append(f'<text x="{width / 2}" y="{height - 5}" text-anchor="middle">{esc(xlabel)}</text>')
    if baseline is not None:
        svg.append(
            f'<line class="baseline" x1="{left}" x2="{width - right}" '
            f'y1="{y(baseline)}" y2="{y(baseline)}"><title>基线 {baseline:.2%}</title></line>'
        )
    if best:
        running = baseline
        best_points = []
        for step in sorted({p["x"] for p in valid}):
            values = [p["y"] for p in valid if p["x"] == step]
            running = max(values + ([running] if running is not None else []))
            best_points.append(f"{x(step)},{y(running)}")
        svg.append(f'<polyline class="best" points="{" ".join(best_points)}"/>')
    legend_items = []
    for index, line in enumerate(series):
        color = COLORS[index % len(COLORS)]
        legend_items.append((line["name"], color, "both", ""))
        previous = None
        for point in sorted(line["points"], key=lambda point: point["x"]):
            if point["y"] is None:
                previous = None  # Do not bridge failed or incomplete evaluations.
                continue
            px, py = x(point["x"]), y(point["y"])
            if previous is not None:
                svg.append(
                    f'<line class="score-line" x1="{previous[0]}" y1="{previous[1]}" '
                    f'x2="{px}" y2="{py}" stroke="{color}"/>'
                )
            title = f"{line['name']} · {point['label']} · {point['y']:.2%}"
            svg.append(
                f'<circle tabindex="0" cx="{px}" cy="{py}" r="5" fill="{color}">'
                f"<title>{esc(title)}</title></circle>"
            )
            previous = (px, py)
    svg.append("</svg>")
    if baseline is not None:
        legend_items.append((f"优化前：{baseline:.2%}", "#8a9691", "line", "8 6"))
    if best:
        legend_items.append(("截至当时的最高分", "#c59835", "line", "4 5"))
    return "".join(svg) + legend(legend_items)


def table(rows: list[tuple[str, float | None, str]]) -> str:
    return '<details><summary>查看逐次评测记录</summary><div class="table-wrap"><table>' + (
        "<thead><tr><th>版本 / 轮次</th><th>分数</th><th>状态 / 说明</th></tr></thead><tbody>"
        + "".join(
            f"<tr><td>{esc(label)}</td><td>{score:.2%}</td><td>{esc(status)}</td></tr>"
            if score is not None
            else f"<tr><td>{esc(label)}</td><td>—</td><td>{esc(status)}</td></tr>"
            for label, score, status in rows
        )
        + "</tbody></table></div></details>"
    )


def rsi_panel(entry: dict[str, Any]) -> str:
    run = RunReader(entry["root"]).detail(entry["run_id"])
    rows = run["score_history"]
    baseline = valid_score(run["baseline_score"])
    series = []
    for agent in run["agents"]:
        points = [{"x": 0, "y": baseline, "label": "基线"}] if baseline is not None else []
        points += [
            {
                "x": row["iteration"],
                "y": valid_score(row["score"]),
                "label": f"第 {row['iteration']} 轮 · {row['solution_id']}",
            }
            for row in rows
            if row["agent_id"] == agent["agent_id"]
        ]
        name = agent["agent_id"].replace("research-agent-", "研究线 ")
        series.append({"name": name, "points": points})
    if not series and baseline is not None:
        series = [{"name": "基线", "points": [{"x": 0, "y": baseline, "label": "基线"}]}]
    active = "运行中 · 本地执行锁活跃" if run["worker_alive"] else f"记录状态：{run['state']}"
    status = " · ".join(
        f"{a['agent_id']} {a['completed_iterations']}/{a['max_iterations']} 轮"
        + (
            f"，评测 {a.get('task') or 0}/{a.get('task_count') or '?'} 题"
            if a["phase"].startswith("evaluate_")
            else ""
        )
        for a in run["agents"]
    )
    if not rows:
        progress = run["baseline_progress"]
        status += f" · 基线评测 {progress.get('task') or 0}/{progress.get('task_count') or '?'} 题"
    labels = {
        "baseline": "基线",
        "selected": "继续采用",
        "reverted": "选择其他版本",
        "stopped": "研究线结束",
        "pending": "待完成版本选择",
    }
    return (
        f'<section class="panel"><div class="eyebrow">RSI · 每轮测试成绩</div>'
        f"<h2>{esc(entry['root'].name)} <small>{esc(run['run_id'])}</small></h2>"
        f'<p>{esc(run["goal"])}</p><p class="meta">{esc(active)} · {esc(run["benchmark"])}</p>'
        f'<p class="meta">{esc(status)}</p>'
        '<p class="note">每条实线代表一条独立尝试改进的研究线。横轴是优化轮次，'
        "纵轴越高，成绩越好。灰线是本实验优化前的成绩，金线保留截至当时的最高分。</p>"
        "<details><summary>这些分数怎么测出来？</summary><p>每次修改后，用实验设置的验证题目"
        "测试新版本（VALIDATE）。修改前的测试（TRAIN）属于旧版本，所以没有混在这张图里。"
        "连线按轮次排列，版本是否继续使用可在下方记录中查看。</p></details>"
        + chart(series, baseline)
        + table(
            [
                (
                    f"{r['agent_id']} / {r['iteration']} · {r['solution_id']}",
                    valid_score(r["score"]),
                    labels[r["selection"]],
                )
                for r in rows
            ]
        )
        + f'<p class="source">来源：{esc(entry["root"])}</p></section>'
    )


def campaign_point(result: dict[str, Any]) -> float | None:
    if result.get("status") != "DONE":
        return None
    value = valid_score(result.get("mean_over_89"))
    total = valid_score(result.get("score_sum"))
    value = value if value is not None else total / 89 if total is not None else None
    return value if value is not None and 0 <= value <= 1 else None


def campaign_panel(entry: dict[str, Any]) -> str:
    root = entry["root"]
    register = read_object(root / "experiments.json")
    progress = read_object(root / "progress.json") if (root / "progress.json").exists() else {}
    validation = (
        read_object(root / "validation-state.json")
        if (root / "validation-state.json").exists()
        else {}
    )
    baseline = campaign_point(progress.get("baseline", {}))
    points = [{"x": 0, "y": baseline, "label": "基线"}]
    rows = [("基线", baseline, str(progress.get("baseline", {}).get("status", "未开始")))]
    for number in sorted(register["rounds"], key=int):
        path = root / "results" / f"round-{int(number):02d}.json"
        result = read_object(path) if path.exists() else progress.get("rounds", {}).get(number, {})
        score = campaign_point(result)
        label = f"轮次 {number}"
        points.append({"x": int(number), "y": score, "label": label})
        counts = result.get("task_counts", result.get("counts", {}))
        rows.append((label, score, f"{result.get('status', '未开始')} · 任务状态 {counts}"))
    attempts = []
    validation_rows = []
    for index, attempt in enumerate(validation.get("attempts", []), 1):
        result = attempt.get("validation", {})
        label = f"复验 {index} · 候选轮次 {attempt.get('round', '?')}"
        score = campaign_point(result)
        attempts.append({"x": index, "y": score, "label": label})
        validation_rows.append((label, score, str(result.get("status", "未开始"))))
    return (
        '<section class="panel"><div class="eyebrow">独立实验 · 全量 89 题</div>'
        f"<h2>Codex / Terminus-2 <small>{esc(root.name)}</small></h2>"
        f'<p class="meta">记录更新时间：{esc(progress.get("observed_at", "未记录"))} · '
        f"复验阶段：{esc(validation.get('phase', '未开始'))}</p>"
        '<p class="note">每轮测试完整 89 题，分数越高越好。灰线是本组实验优化前的成绩，'
        "金线保留截至当时的最高分。尚未测完的轮次留空。</p>"
        "<details><summary>评分与记录说明</summary><p>图表读取本地记录，不实时查询 Arena。"
        "保留原始评分；运行失败的题目可能仍包含在原始评分分母中。</p></details>"
        "<h3>每轮优化后的成绩</h3>"
        + chart([{"name": "每轮得分", "points": points}], baseline)
        + table(rows)
        + '<h3>选出的版本，再测一次</h3><p class="meta">同一版本的成绩也可能波动。'
        "为了检查好成绩是不是偶然，将选中的版本再测一遍完整 89 题，这叫“复验”。"
        "这里按复验次序展示分数；这一步不修改版本，所以单独画图。</p>"
        + chart(
            [{"name": "再次测试的得分", "points": attempts}],
            baseline,
            xlabel="再次测试的次序",
            best=False,
        )
        + table(validation_rows)
        + f'<p class="source">来源：{esc(root)}</p></section>'
    )


def create_html_report(
    root: Path,
    run_id: str | None = None,
    *,
    query: str = "",
    scope: str = "recent",
    limit: int = 4,
    offset: int = 0,
    open_browser: bool = True,
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
    for entry in entries:
        entry["active"] = entry["kind"] == "rsi" and RunReader(entry["root"]).worker_alive(
            entry["run_id"]
        )
    if scope == "running":
        entries = [entry for entry in entries if entry["active"]]
    elif scope == "all":
        entries.sort(key=lambda entry: (entry["active"], entry["started_at"]), reverse=True)
    if not entries:
        return "当前工作区没有实验记录。" if not query and scope == "recent" else "没有匹配的实验。"
    if run_id and len(entries) > 1:
        return "实验 ID 存在于多个目录，请用 query 指定目录区分。"
    selected = entries[offset : offset + (1 if scope == "recent" else limit)]
    panels = []
    for entry in selected:
        try:
            panels.append(rsi_panel(entry) if entry["kind"] == "rsi" else campaign_panel(entry))
        except (OSError, ValueError, KeyError, TypeError):
            warnings.append(f"实验 {entry['run_id']} 记录不完整或不兼容，已跳过。")
    generated = datetime.now().astimezone().isoformat(timespec="seconds")
    style = Path(__file__).with_name("static").joinpath("report.css").read_text()
    document = (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "style-src 'unsafe-inline'; img-src data:; base-uri 'none'\">"
        f"<title>RSI · 优化过程</title><style>{style}</style></head><body><main>"
        '<header><div class="eyebrow">AREAL RSI / EXPERIMENT OBSERVATORY</div>'
        "<h1>Task Agent 优化过程</h1>"
        "<p>Task Agent 的实测表现、历史最佳与独立复验。</p>"
        f'<div class="snapshot">生成于 {esc(generated)} · '
        f"{len(panels)} 个实验 · 离线报告</div></header>"
        "<aside>不同 Benchmark 和数据分区分别展示，不合并排名。悬停或聚焦节点查看具体分数；"
        "展开记录查看版本和状态。此页为生成时快照，在 TUI 说“刷新刚才的图”获取最新报告。</aside>"
        + "".join(f'<p class="warning">{esc(warning)}</p>' for warning in warnings)
        + "".join(panels)
        + "<footer>由本地评测记录生成 · 无需 Web 服务或网络图表依赖</footer></main></body></html>"
    )
    folder = root.resolve() / "visualizations"
    folder.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(json.dumps([run_id, query, scope, limit, offset]).encode()).hexdigest()[
        :16
    ]
    path = folder / f"scores-{key}.html"
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=folder, delete=False
    ) as stream:
        temporary = Path(stream.name)
        stream.write(document)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    opened = False
    if open_browser:
        with suppress(OSError, webbrowser.Error):
            opened = webbrowser.open(path.as_uri(), new=2)
    return json.dumps(
        {
            "format": "html",
            "report_path": str(path),
            "report_url": path.as_uri(),
            "browser_open_requested": opened,
            "experiment_count": len(panels),
            "matched_count": len(entries),
            "generated_at": generated,
            "warnings": warnings,
            "next_offset": offset + len(selected)
            if offset + len(selected) < len(entries)
            else None,
            "summary": "已生成 HTML 折线图报告。返回简短摘要和报告链接，不在聊天中重画字符图。",
        },
        ensure_ascii=False,
    )
