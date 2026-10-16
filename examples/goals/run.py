"""Real model demo runner; executes the public CLI and preserves every event."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def model_contents(root: Path, identity: dict[str, Any]) -> dict[str, Any]:
    """Compare immutable model contents across roots, whose generated IDs differ."""
    result = {}
    for role, entry in identity.get("agent_models", {}).items():
        version = json.loads((root / "versions" / (entry["version_id"] + ".json")).read_text())
        result[role] = {"model": version["name"], "content": version["content"]}
    return {"agents": result, "interaction_model_name": identity.get("interaction_model_name")}


def run_goal(name: str, root: Path, *, skill: Path | None = None) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    receipt = root / "demo-result.json"
    if receipt.exists():
        from areal_rsi.goals import GoalService

        result: dict[str, Any] = json.loads(receipt.read_text())
        if result.get("goal_id"):
            result = {**result, **GoalService(root).status(result["goal_id"])}
        return result
    if any((root / "goals").glob("*.json")):
        raise ValueError("This root already has a Goal; inspect or resume it with rsi goal")
    spec = json.loads((HERE / f"{name}.json").read_text())
    for key in ("artifact_path",):
        if spec.get(key):
            spec[key] = str(HERE / Path(spec[key]).name)
    if spec.get("assets", {}).get("harness_path"):
        spec["assets"]["harness_path"] = str(HERE / "minimal-harness")
    spec["skill_paths"] = [str(skill.resolve())] if skill else []
    spec_file = root / "submitted-goal.json"
    spec_file.write_text(json.dumps(spec, indent=2))
    protocol = (HERE / "protocol.json").read_bytes()
    (root / "protocol.json").write_bytes(protocol)
    (root / "protocol.sha256").write_text(hashlib.sha256(protocol).hexdigest() + "\n")
    env = {**os.environ, "RSI_DATA_ROOT": str(root.resolve())}
    events = root / "cli-events.ndjson"
    with events.open("w") as output, (root / "cli-diagnostics.log").open("w") as errors:
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "areal_rsi",
                "exec",
                "--spec",
                str(spec_file),
                "--yes",
                "--json",
            ],
            env=env,
            stdout=output,
            stderr=errors,
        )
    parsed = [json.loads(line) for line in events.read_text().splitlines()]
    result = next(
        (event for event in reversed(parsed) if event.get("type") == "result"),
        {"state": "entrypoint_failed"},
    )
    result["cli_exit_code"] = process.returncode
    receipt.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(
        json.dumps(
            {
                "demo": name,
                "root": str(root),
                "state": result["state"],
                "exit_code": process.returncode,
                "goal_id": result.get("goal_id"),
            }
        ),
        flush=True,
    )
    return result


def reuse(root: Path, skill: Path) -> None:
    # AB / BA / AB order is frozen before results. Each root has independent Memory and caches.
    activation = skill.parent / "record.json"
    if not activation.exists() or json.loads(activation.read_text()).get("state") != "active":
        raise ValueError("The reuse experiment requires an explicitly activated Skill version")
    rows = []
    common_identity = None
    for pair in range(1, 4):
        for group in ("skill", "control") if pair == 2 else ("control", "skill"):
            path = root / f"pair-{pair}-{group}"
            result = run_goal("bookmark", path, skill=skill if group == "skill" else None)
            frozen = result.get("frozen_identity", {})
            baseline = frozen.get("baseline", {})
            identity = {
                "model": baseline.get("model", {}).get("content"),
                "harness": baseline.get("harness", {}).get("content"),
                "artifacts": [a["content"] for a in baseline.get("artifacts", [])],
                "runtime": (result.get("researcher") or {}).get("runtime_digest"),
                "research_model": model_contents(
                    path, (result.get("researcher") or {}).get("model_identity", {})
                ),
                "tasks": frozen.get("tasks"),
                "verifier": frozen.get("verifier"),
                "implementation": frozen.get("implementation"),
                "goal_spec_without_skill_paths": {
                    key: value
                    for key, value in result.get("spec", {}).items()
                    if key != "skill_paths"
                },
                "budget": result.get("spec", {}).get("budget"),
                "acceptance": result.get("spec", {}).get("acceptance"),
            }
            if common_identity is None:
                common_identity = identity
            elif identity != common_identity:
                raise ValueError(
                    "Paired runs differ beyond the selected Skill; "
                    "preserve evidence and fix protocol"
                )
            rows.append(
                {
                    "pair": pair,
                    "group": group,
                    "state": result["state"],
                    "goal_id": result.get("goal_id"),
                    "root": str(path),
                    "seconds": (result.get("outcome") or {}).get("elapsed_seconds"),
                    "token_usage": (result.get("outcome") or {}).get("token_usage"),
                    "cost": (result.get("outcome") or {}).get("cost"),
                }
            )
    success = {
        group: sum(r["state"] == "achieved" for r in rows if r["group"] == group) / 3
        for group in ("control", "skill")
    }
    medians = {
        group: statistics.median(r["seconds"] for r in rows if r["group"] == group)
        for group in ("control", "skill")
        if all(isinstance(r["seconds"], (float, int)) for r in rows if r["group"] == group)
    }
    reduction = 1 - medians["skill"] / medians["control"] if len(medians) == 2 else None
    totals = {
        group: sum(r["seconds"] for r in rows if r["group"] == group)
        for group in medians
    }
    total_increase = totals["skill"] / totals["control"] - 1 if len(totals) == 2 else None
    report = {
        "protocol": json.loads((HERE / "protocol.json").read_text())["reuse"],
        "runs": rows,
        "common_identity": common_identity,
        "only_treatment_difference": "frozen research Skill version",
        "success_rates": success,
        "median_seconds_all_runs": medians,
        "sum_seconds_all_runs": totals,
        "skill_total_time_increase": total_increase,
        "time_reduction": reduction,
        "time_gate_passed": all(v == 1 for v in success.values())
        and reduction is not None
        and reduction >= 0.2,
        "efficiency_proven": False,
        "limitation": (
            "Token/fee/human non-regression gate requires observable costs; "
            "three pairs are demo evidence only."
        ),
    }
    (root / "reuse-report.json").write_text(json.dumps(report, indent=2))
    record = json.loads(activation.read_text())
    # Rebuilding a report from receipts must not create a second experiment.
    record["cross_task_evidence"] = [
        item for item in record.get("cross_task_evidence", []) if item.get("root") != str(root)
    ]
    record["cross_task_evidence"].append(
        {
            "root": str(root),
            "success_rates": success,
            "time_reduction": reduction,
            "time_gate_passed": report["time_gate_passed"],
            "sum_seconds_all_runs": totals,
            "skill_total_time_increase": total_increase,
            "efficiency_proven": False,
        }
    )
    activation.write_text(json.dumps(record, indent=2))
    source_root = record.get("provenance", {}).get("data_root")
    if source_root:
        reports = Path(source_root) / "skill-reuse"
        reports.mkdir(parents=True, exist_ok=True)
        (
            reports / (record["skill"]["skill_id"] + "-" + record["skill"]["version"] + ".json")
        ).write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("demo", choices=["harness", "artifact", "reuse"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--skill", type=Path)
    args = parser.parse_args()
    if args.demo == "reuse":
        if args.skill is None:
            parser.error("reuse requires an explicitly activated --skill package")
        reuse(args.root.resolve(), args.skill)
    else:
        run_goal("task" if args.demo == "artifact" else "harness", args.root.resolve())


if __name__ == "__main__":
    main()
