"""Bind an existing single-task GPUQ evaluation to a workbench candidate.

This adapter only reads the queue. It never registers tasks or submits GPU work.
The operator pins the existing task, worker files and comparison conditions in a
manifest; the candidate is hashed without being imported or executed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

TERMINAL = {"succeeded", "failed", "cancelled", "timed_out", "interrupted"}
PRECISION = {
    "cudnn_allow_tf32": False,
    "matmul_allow_tf32": False,
    "float32_matmul_precision": "highest",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_queue(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Remote side: read authenticated records and their frozen input files."""
    root = Path(manifest["kb_root"])
    for relative, expected in manifest["worker_hashes"].items():
        if sha(root / relative) != expected:
            raise ValueError("Pinned evaluation worker changed: " + relative)
    sys.path.insert(0, str(root))
    from common import request  # type: ignore[import-not-found]

    token = Path(manifest["token_file"]).read_text().strip()
    jobs = request("/jobs", token=token, url=manifest["gpuq_url"], timeout=30)
    if len({j["id"] for j in jobs}) != len(jobs):
        raise ValueError("Duplicate queue job identifiers")
    result = []
    for job in jobs:
        if job["task_id"] != manifest["task_id"] or job["budget"] != manifest["budget"]:
            raise ValueError("Unexpected task ownership or evaluation budget")
        item = {
            key: job[key] for key in ("id", "task_id", "kind", "state", "created_at", "budget_used")
        }
        item.update(source_sha256=None, evaluation={})
        if job["kind"] == "eval" and job["state"] in TERMINAL:
            workspace = Path(job["cwd"])
            source = workspace / "model_new.py"
            spec_path = workspace.parent / "evaluation-spec.json"
            spec = json.loads(spec_path.read_text())
            if job["argv"] != [
                manifest["python"],
                "-u",
                str(root / "kb_worker.py"),
                str(spec_path),
            ]:
                raise ValueError("Formal job did not use the pinned worker")
            for key in ("level", "problem", "seed"):
                if spec.get(key) != manifest[key]:
                    raise ValueError("Formal job has different comparison conditions: " + key)
            if spec.get("mode") != "eval" or Path(spec["candidate"]) != source:
                raise ValueError("Formal job did not evaluate its frozen candidate")
            item["source_sha256"] = sha(source)
            # The stdout marker precedes strict_worker adding its precision
            # identity. Read the final result file, not that incomplete marker.
            output = workspace.parent / "evaluation.json"
            if Path(spec["output"]) != output:
                raise ValueError("Formal result is outside the job directory")
            evaluation = json.loads(output.read_text()) if output.is_file() else {}
            if evaluation != (job.get("evaluation") or {}):
                raise ValueError("Queue result and formal result file disagree")
            item["evaluation"] = evaluation
        result.append(item)
    if len(result) > manifest["budget"]:
        raise ValueError("Existing task exceeded its submission budget")
    return result


def query(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    # Verify the remote adapter before executing it; no candidate code is run.
    bootstrap = (
        "import hashlib,runpy,sys;from pathlib import Path;"
        "p=Path(sys.argv[1]);"
        "assert hashlib.sha256(p.read_bytes()).hexdigest()==sys.argv[2];"
        "sys.argv=[str(p),'--read-remote'];runpy.run_path(str(p),run_name='__main__')"
    )
    command = [
        "bash",
        manifest["continue_script"],
        manifest["slurm_job"],
        "--",
        "env",
        "CUDA_VISIBLE_DEVICES=",
        manifest["python"],
        "-B",
        "-c",
        bootstrap,
        manifest["remote_script"],
        manifest["reader_sha256"],
    ]
    response = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            manifest["host"],
            shlex.join(command),
        ],
        input=json.dumps(manifest),
        capture_output=True,
        text=True,
        timeout=50,
    )
    if response.returncode:
        # Do not forward SSH output, which may contain environment credentials.
        raise RuntimeError(f"Read-only queue lookup failed (exit {response.returncode})")
    return json.loads(response.stdout)  # type: ignore[no-any-return]


def valid_speedup(job: dict[str, Any], digest: str | None, manifest: dict[str, Any]) -> float:
    value = job["evaluation"]
    if not (
        digest is not None
        and job["state"] == "succeeded"
        and value.get("correct") is True
        and value.get("compiled") is True
        and value.get("numerical_correct") is True
        and value.get("source_sha256") == digest
        and value.get("evaluation_policy") == "strict-fp32-v1"
        and value.get("reference_precision") == PRECISION
        and value.get("seed") == manifest["seed"]
        and value.get("num_correct_trials") == 5
        and value.get("num_perf_trials") == 100
        and value.get("metadata", {}).get("correctness_trials") == "(5 / 5)"
    ):
        return 0.0
    numbers = [value.get(key) for key in ("speedup", "runtime_ms", "ref_ms")]
    if any(type(n) not in (int, float) or not math.isfinite(n) or n <= 0 for n in numbers):
        return 0.0
    speedup, runtime, reference = numbers
    return float(speedup) if math.isclose(speedup, reference / runtime, rel_tol=1e-9) else 0.0


def score(
    manifest: dict[str, Any], workspace: Path, jobs: list[dict[str, Any]], phase: str
) -> tuple[float, bool, dict[str, Any]]:
    if phase not in {"search", "final"}:
        raise ValueError("Only search and final verification are supported")
    if len({j["id"] for j in jobs}) != len(jobs):
        raise ValueError("An independent repeat requires distinct job identifiers")
    source = workspace / "model_new.py"
    digest = sha(source) if source.is_file() and not source.is_symlink() else None
    matches = sorted(
        (
            j
            for j in jobs
            if j["kind"] == "eval"
            and j["state"] in TERMINAL
            and digest is not None
            and j["source_sha256"] == digest
        ),
        key=lambda j: (j["created_at"], j["id"]),
    )
    valid = [(valid_speedup(j, digest, manifest), j) for j in matches]
    if phase == "final":
        selected = valid[-1] if len(matches) >= 2 else (0.0, None)
    else:
        selected = max(valid, key=lambda item: item[0], default=(0.0, None))
    speedup, chosen = selected
    # Some existing briefs express fast1 in prose with target=null. Enforce it
    # here so finalization cannot accept a correct but slower implementation.
    passed = speedup > (1.0 if phase == "final" else 0.0)
    return (
        speedup,
        passed,
        {
            "source_sha256": digest,
            "matching_evaluations": len(matches),
            "formal_job_ids": [j["id"] for j in matches],
            "selected_job": chosen,
            "gpu_submissions": len(jobs),
            "phase": phase,
        },
    )


def verify(manifest: dict[str, Any], workspace: Path, output: Path, phase: str) -> dict[str, Any]:
    snapshot = json.loads((workspace.parent / "snapshot.json").read_text())
    run_root = workspace.parents[2]
    job = json.loads((run_root / "jobs" / output.name / "job.json").read_text())
    metadata = job["spec"]["metadata"]
    if snapshot["digest"] != metadata["candidate_digest"] or metadata["phase"] != phase:
        raise ValueError("Verifier snapshot or phase differs from the frozen job")
    speedup, passed, evidence = score(manifest, workspace, query(manifest), phase)
    result = {
        "status": "completed",
        "simulated": False,
        "phase": phase,
        "metric": manifest["metric"],
        "score": speedup,
        "passed": passed,
        "metrics": {
            "speedup": speedup,
            "fast_1": float(speedup > 1),
            "gpu_submissions": evidence["gpu_submissions"],
            "matching_evaluations": evidence["matching_evaluations"],
        },
        "candidate_id": metadata["candidate_id"],
        "candidate_digest": snapshot["digest"],
        "protocol_id": manifest["protocol_id"],
        "manifest_id": manifest["manifest_id"],
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "public").mkdir(exist_ok=True)
    (output / "public" / "feedback.json").write_text(json.dumps(evidence, ensure_ascii=False))
    (output / "evaluation.json").write_text(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return result


def main() -> None:
    if sys.argv[1:] == ["--read-remote"]:
        print(json.dumps(read_queue(json.load(sys.stdin))))
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--phase", choices=("search", "final"), required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            verify(json.loads(args.manifest.read_text()), args.workspace, args.output, args.phase)
        )
    )


if __name__ == "__main__":
    main()
