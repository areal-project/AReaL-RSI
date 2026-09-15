"""Read-only discovery of historical workspaces and standalone TB campaigns.

Discovery deliberately skips trajectories, dependencies and task artifacts. It does
not register old runs with the experiment controller or make them resumable.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

SKIP = {
    "content",
    "versions",
    "runs",
    "run-states",
    "sessions",
    "trajectories",
    "research-workspaces",
    "node_modules",
    "dsh-home",
    "dsh-cli",
    "datasets",
    "workers",
    "gateway",
    "audits",
    "results",
    ".git",
    ".venv",
}


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("Expected JSON object")
    return value


def discover(root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    root = root.resolve()
    entries: list[dict[str, Any]] = []
    warnings: list[str] = []
    for index, (directory, children, files) in enumerate(os.walk(root, followlinks=False)):
        if index >= 2000:
            warnings.append("目录扫描达到 2000 个上限，请指定更小的工作区。")
            break
        folder = Path(directory)
        depth = len(folder.relative_to(root).parts)
        children[:] = sorted(
            name
            for name in children
            if name not in SKIP
            and not name.startswith(".")
            and depth < 4
            and not (folder / name).is_symlink()
        )
        states = folder / "run-states"
        if states.is_dir() and not states.is_symlink():
            for path in sorted(states.glob("*.json")):
                if path.is_symlink():
                    continue
                try:
                    value = read_object(path)
                    plan = value["plan"]
                    if value["run_id"] != path.stem:
                        raise ValueError("Run identity mismatch")
                    entries.append(
                        {
                            "kind": "rsi",
                            "root": folder,
                            "run_id": path.stem,
                            "benchmark": plan["benchmark"],
                            "goal": plan["goal"],
                            "state": value["state"],
                            "started_at": value.get("started_at") or "",
                        }
                    )
                except (OSError, ValueError, KeyError, TypeError):
                    warnings.append(f"无法读取实验索引：{path.relative_to(root)}")
        if "experiments.json" in files and not (folder / "experiments.json").is_symlink():
            try:
                register = read_object(folder / "experiments.json")
                # This adapter recognizes the standalone full-TB campaign schema.
                if not all(k in register for k in ("baseline_job", "benchmark_ref", "rounds")):
                    continue
                benchmark = register["benchmark_ref"]
                if not isinstance(benchmark, dict) or "tb21" not in benchmark.get("key", ""):
                    continue
                entries.append(
                    {
                        "kind": "tb_campaign",
                        "root": folder,
                        "run_id": folder.name,
                        "benchmark": benchmark["key"],
                        "goal": "Codex 优化 Terminus-2",
                        "state": "snapshot",
                        "started_at": register.get("created_at", ""),
                    }
                )
            except (OSError, ValueError, KeyError, TypeError):
                warnings.append(f"无法读取独立实验：{folder.relative_to(root)}")
    return sorted(entries, key=lambda entry: entry["started_at"], reverse=True), warnings


def matches(entry: dict[str, Any], query: str) -> bool:
    def normalize(text: str) -> str:
        return text.casefold().replace("terminal-bench", "tb").replace("terminal bench", "tb")

    haystack = normalize(
        " ".join(
            str(entry[key])
            for key in (
                "root",
                "run_id",
                "benchmark",
                "goal",
            )
        )
    )
    return all(word in haystack for word in normalize(query).split())
