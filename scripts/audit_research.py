"""Audit a completed real Harness research run; never infer success from a log line."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def read(path: Path) -> Any:
    return json.loads(path.read_text())


def audit(root: Path, run_id: str) -> dict[str, Any]:
    run = root / "runs" / run_id
    state = read(run / "coordination/state.json")
    assert state["status"] == "succeeded", "Research has not completed"
    assert state["simulated"] is False, "Simulation is not live acceptance"
    assert state["model_calls"] > 0, "No research model calls"
    reports = list((run / "reports").glob("*.json"))
    assert reports, "No researcher report"
    seed = read(run / "candidates" / f"{state['seed_candidate_id']}.json")
    candidates = [read(p) for p in (run / "candidates").glob("*.json")]
    changed = {
        c["candidate_id"] for c in candidates if c["content"]["digest"] != seed["content"]["digest"]
    }
    assert changed, "No actual candidate change"
    jobs = read(run / "coordination/jobs.json")
    checks = [read(p) for p in (run / "verifications").glob("*.json")]
    assert any(
        v["phase"] == "search" and v["candidate_id"] in changed and v["status"] == "succeeded"
        for v in checks
    ), "No evaluated changed candidate"
    final = [
        v
        for v in checks
        if v["phase"] == "final"
        and v["status"] == "succeeded"
        and v["candidate_id"] == state["selected_candidate_id"]
    ]
    assert final, "No final verification for selected candidate"
    train = [
        key
        for key, j in jobs.items()
        if not j["verification"] and j["spec"]["metadata"]["command_template"] == "develop"
    ]
    assert train, "No development task execution"
    task_sets: dict[str, list[str]] = {}
    remote_jobs = []
    for key, job in jobs.items():
        receipt = read(run / "jobs" / key / "job.json")
        assert receipt["spec"]["backend"] != "mock", "Mock task job"
        assert receipt["state"] == "succeeded" and receipt["exit_code"] == 0, "Execution failed"
        output = Path(job["output"])
        evaluation = read(output / "evaluation.json")
        assert evaluation["simulated"] is False and evaluation["status"] == "completed"
        rows = read(output / "private/results.json")
        assert rows and all(r["trajectory"]["trajectory_id"] for r in rows), "Missing target traces"
        phase = "train" if not job["verification"] else job["phase"]
        task_sets[phase] = sorted(r["task_id"] for r in rows)
        for path in (output / "private/remote/executions").glob("*/receipt.json"):
            remote = read(path)
            assert remote["state"] == "collected", "Remote job incomplete"
            remote_jobs.append(remote["job_id"])
    assert set(task_sets["train"]).isdisjoint(task_sets["search"]), "Training and holdout overlap"
    assert task_sets["search"] == task_sets["final"], "Unexpected final task identity"
    return {
        "run_id": run_id,
        "status": "succeeded",
        "model_calls": state["model_calls"],
        "reports": len(reports),
        "changed_candidates": sorted(changed),
        "final_candidate": state["selected_candidate_id"],
        "final_score": final[-1]["score"],
        "tasks": task_sets,
        "remote_jobs": remote_jobs,
        "verification_count": len(checks),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--run", required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.root.resolve(), args.run), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
