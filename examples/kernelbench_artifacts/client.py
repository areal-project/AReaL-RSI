"""GPUQ evaluation jobs and read-only verification of a collected task bundle."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

from areal_rsi.base.storage import write_json

TERMINAL = {"succeeded", "failed", "cancelled", "timed_out", "interrupted"}


def remote(manifest: dict[str, Any], operation: str, **values: Any) -> Any:
    command = [
        "bash",
        manifest["continue_script"],
        manifest["slurm_job"],
        "--",
        manifest.get("python", "/opt/.venv/bin/python"),
        "-B",
        manifest["remote_script"],
        manifest["remote_manifest"],
    ]
    result = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            manifest["host"],
            shlex.join(command),
        ],
        input=json.dumps({"operation": operation, **values}),
        capture_output=True,
        text=True,
        timeout=50,
    )
    try:
        response = json.loads(result.stdout)
    except ValueError:
        raise RuntimeError(f"SSH bridge returned no result (exit {result.returncode})") from None
    if not response["ok"]:
        raise RuntimeError(response["error"])
    return response["value"]


def evaluate(
    manifest: dict[str, Any], workspace: Path, output: Path, task: str, candidate: str
) -> None:
    source = input_source(workspace, candidate)
    submit_and_wait(manifest, output, task, "submit", source=source)
    feedback = json.loads((output / "feedback.json").read_text())
    if feedback.get("correct") is True:
        # Publish this job's exact input, never another branch's global best.
        artifact = output / "candidate" / task
        artifact.mkdir(parents=True, exist_ok=True)
        (artifact / "model_new.py").write_bytes(base64.b64decode(source, validate=True))


def input_source(workspace: Path, candidate: str) -> str:
    path = (workspace / candidate).resolve()
    if not path.is_relative_to(workspace.resolve()) or not path.is_file():
        raise ValueError("Input must be a file in the frozen job workspace")
    content = path.read_bytes()
    if len(content) > 2_000_000:
        raise ValueError("Input exceeds 2 MB")
    return base64.b64encode(content).decode()


def diagnose(
    manifest: dict[str, Any],
    workspace: Path,
    output: Path,
    task: str,
    script: str,
    candidate: str = "",
) -> None:
    script_source = input_source(workspace, script)
    compile(base64.b64decode(script_source), "_rsi_diagnostic.py", "exec")
    source = input_source(workspace, candidate) if candidate else ""
    submit_and_wait(
        manifest, output, task, "submit_diagnostic", source=source, script=script_source
    )


def submit_and_wait(
    manifest: dict[str, Any], output: Path, task: str, operation: str, **sources: str
) -> None:
    if task not in manifest["tasks"]:
        raise ValueError("Task is outside this research run")
    output.mkdir(parents=True, exist_ok=True)
    record_path = output / "remote-job.json"
    budget = manifest.get("submission_budget_per_task", 11)
    while True:
        try:
            if record_path.exists():
                job = remote(
                    manifest, "job", task=task, job_id=json.loads(record_path.read_text())["id"]
                )
            else:
                job = remote(manifest, operation, task=task, key=output.name, **sources)
            write_json(record_path, job)
            budget_message = (
                f"本题已提交 {job.get('task_budget_used', job['budget_used'])} 次，次数不限"
                if budget is None
                else f"本题已用 {job['task_budget_used']}/{budget} 次 GPU 提交，"
                f"剩余 {job['task_budget_remaining']} 次"
                if "task_budget_used" in job
                else f"本次回执计数 {job['budget_used']}/{budget}；最新总数请查 status"
            )
            write_json(
                output / "progress.json",
                {
                    "message": f"{task}：{job['state']}，{budget_message}",
                    "stage": "远端诊断" if operation == "submit_diagnostic" else "远端评测",
                    "completed": int(job["state"] in TERMINAL),
                    "total": 1,
                },
            )
            if job["state"] in TERMINAL:
                write_json(output / "feedback.json", {**job, "task": task})
                if operation == "submit_diagnostic":
                    logs = remote(manifest, "logs", task=task, job_id=job["id"])
                    for stream in ("stdout", "stderr"):
                        (output / (stream + ".log")).write_text(logs[stream]["text"])
                    write_json(
                        output / "diagnostic-logs.json",
                        {
                            s: {k: v for k, v in data.items() if k != "text"}
                            for s, data in logs.items()
                        },
                    )
                print(json.dumps({"task": task, **job}, ensure_ascii=False))
                return  # A measured incorrect candidate is feedback, not an infrastructure failure.
        except (subprocess.TimeoutExpired, RuntimeError) as exc:
            if "budget exhausted" in str(exc):
                write_json(
                    output / "feedback.json",
                    {"task": task, "budget_exhausted": True, "error": str(exc)},
                )
                print(str(exc))
                return
            if str(exc).startswith(("ValueError:", "SyntaxError:", "AssertionError:")) or any(
                code in str(exc)
                for code in (
                    "GPUQ HTTP 400",
                    "GPUQ HTTP 401",
                    "GPUQ HTTP 403",
                    "GPUQ HTTP 404",
                    "Submission ID reused",
                )
            ):
                raise
            write_json(output / "progress.json", {"stage": "连接重试", "message": str(exc)[:400]})
        time.sleep(5)


def score_bundle(
    manifest: dict[str, Any], workspace: Path, status: dict[str, Any], phase: str
) -> tuple[float, dict[str, float], dict[str, Any]]:
    metrics: dict[str, float] = {}
    evidence: dict[str, Any] = {}
    for task in manifest["tasks"]:
        path = workspace / task / "model_new.py"
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        matches = [
            j
            for j in status[task]["jobs"]
            if j["kind"] == "eval"
            and j["state"] in TERMINAL
            and digest is not None
            and j["source_sha256"] == digest
        ]
        valid = [
            j
            for j in matches
            if j["correct"]
            and (j.get("evaluation") or {}).get("evaluation_policy") == "strict-fp32-v1"
        ]
        if phase == "search" and "exploration_seed" in manifest:
            valid = [
                j
                for j in valid
                if (j.get("evaluation") or {}).get("seed") == manifest["exploration_seed"]
            ]
        if phase == "final":
            chosen = max(matches, key=lambda j: j["created_at"], default=None)
            if len(matches) < 2 or chosen not in valid:
                chosen = None
        else:
            chosen = max(valid, key=lambda j: j["speedup"], default=None)
        # A baseline-matched run reserves its final request for a new seed.
        if (
            phase == "final"
            and "final_seed" in manifest
            and chosen is not None
            and (chosen.get("evaluation") or {}).get("seed") != manifest["final_seed"]
        ):
            chosen = None
        speedup = chosen["speedup"] if chosen else 0.0
        metrics[task + "_speedup"] = speedup
        metrics[task + "_fast1"] = float(speedup > 1.0)
        metrics[task + "_submissions"] = status[task]["used"]
        evidence[task] = {"sha256": digest, "job": chosen, "matching_evaluations": len(matches)}
    score = sum(metrics[task + "_fast1"] for task in manifest["tasks"]) / len(manifest["tasks"])
    metrics["fast_1"] = score
    if manifest.get("metric") == "speedup":
        if len(manifest["tasks"]) != 1:
            raise ValueError("speedup 指标仅用于单题研究")
        task = next(iter(manifest["tasks"]))
        score = metrics[task + "_speedup"]
        metrics["speedup"] = score
    return score, metrics, evidence


def collect_bundle(
    manifest: dict[str, Any], workspace: Path, excluded: list[str]
) -> dict[str, Any]:
    """Collect frozen evaluated sources, with review exclusions recorded separately."""
    status: dict[str, Any] = remote(manifest, "collect", exclude_sha256=excluded)
    sources = {}
    for task, value in status.items():
        if "source" not in value:
            if (workspace / task / "model_new.py").exists():
                raise ValueError(f"No selected source for {task}; collect into a fresh directory")
            continue
        source = base64.b64decode(value["source"], validate=True)
        digest = hashlib.sha256(source).hexdigest()
        if digest in excluded or digest != value["best"]["source_sha256"]:
            raise ValueError(f"Collected source for {task} does not match the selected snapshot")
        sources[task] = source
    # Validate every source before touching the destination. Older bridges that
    # ignore exclusions must not silently reintroduce a rejected candidate.
    for task, source in sources.items():
        path = workspace / task / "model_new.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(source)
        status[task].pop("source")
    write_json(workspace / "evaluation-sources.json", status)
    return status


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation", choices=("evaluate", "diagnose", "status", "collect", "verify")
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--task")
    parser.add_argument("--candidate")
    parser.add_argument("--script")
    parser.add_argument("--phase", default="search", choices=("search", "final"))
    parser.add_argument("--exclude-sha256", action="append", default=[])
    args = parser.parse_args()
    if args.exclude_sha256 and args.operation != "collect":
        parser.error("--exclude-sha256 only applies to collect")
    manifest = json.loads(args.manifest.read_text())
    if args.operation == "evaluate":
        evaluate(manifest, args.workspace, args.output, args.task, args.candidate)
        return
    if args.operation == "diagnose":
        if not args.script:
            parser.error("diagnose requires --script")
        diagnose(
            manifest, args.workspace, args.output, args.task, args.script, args.candidate or ""
        )
        return
    if args.operation == "collect" and not (args.output or args.workspace):
        parser.error("collect requires --output or --workspace")
    status = (
        collect_bundle(manifest, args.output or args.workspace, args.exclude_sha256)
        if args.operation == "collect"
        else remote(manifest, "status")
    )
    if args.operation == "status":
        print(
            json.dumps(
                {
                    task: {
                        "used": value["used"],
                        "remaining": value["remaining"],
                        "best_speedup": max((j["speedup"] for j in value["jobs"]), default=0),
                        "recent_jobs": [
                            {
                                **{k: j[k] for k in ("id", "kind", "state", "correct", "speedup")},
                                "error": str(j["evaluation"].get("error", ""))[:600],
                            }
                            for j in value["jobs"][-3:]
                        ],
                    }
                    for task, value in status.items()
                },
                ensure_ascii=False,
            )
        )
    elif args.operation == "collect":
        summary = {task: {"used": v["used"], "best": v.get("best")} for task, v in status.items()}
        if args.output:
            write_json(args.output / "feedback.json", summary)
        print(
            json.dumps(
                summary,
                ensure_ascii=False,
            )
        )
    else:
        score, metrics, evidence = score_bundle(manifest, args.workspace, status, args.phase)
        snapshot = json.loads((args.workspace.parent / "snapshot.json").read_text())
        run_root = args.workspace.parents[2]
        execution = json.loads((run_root / "jobs" / args.output.name / "job.json").read_text())
        metadata = execution["spec"]["metadata"]
        assert snapshot["digest"] == metadata["candidate_digest"]
        result = {
            "status": "completed",
            "simulated": False,
            "phase": args.phase,
            "metric": manifest.get("metric", "fast_1"),
            "score": score,
            "passed": score > 0 if manifest.get("metric") == "speedup" else True,
            "metrics": metrics,
            "candidate_id": metadata["candidate_id"],
            "candidate_digest": metadata["candidate_digest"],
            "protocol_id": manifest["protocol_id"],
            "manifest_id": manifest["manifest_id"],
        }
        write_json(args.output / "evaluation.json", result)
        write_json(args.output / "public" / "feedback.json", {"score": score, "tasks": evidence})
        print(json.dumps(result))


if __name__ == "__main__":
    main()
