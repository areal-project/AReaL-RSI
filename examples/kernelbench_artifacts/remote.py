"""Run-scoped bridge to an existing GPUQ service; never starts a GPU scheduler."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import math
import sys
import urllib.error
from pathlib import Path
from typing import Any


def summarize(job: dict[str, Any]) -> dict[str, Any]:
    evaluation = job.get("evaluation") or {}
    speedup = evaluation.get("speedup", 0)
    correct = evaluation.get("correct") is True and job["state"] == "succeeded"
    valid = isinstance(speedup, (int, float)) and math.isfinite(speedup) and speedup > 0
    source = Path(job["cwd"]) / "model_new.py"
    digest = hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() else None
    return {
        **{
            key: job.get(key)
            for key in (
                "id",
                "task_id",
                "kind",
                "state",
                "budget_used",
                "budget",
                "created_at",
                "started_at",
                "finished_at",
                "artifacts",
                "queue_seconds",
                "run_seconds",
            )
        },
        "source_sha256": digest,
        "correct": correct,
        "speedup": float(speedup) if correct and valid else 0.0,
        "evaluation": evaluation,
        "stderr_tail": str(job.get("stderr_tail", ""))[-12000:],
    }


def handle(manifest: dict[str, Any], request_data: dict[str, Any]) -> Any:
    sys.path.insert(0, manifest["kb_root"])
    from common import request

    operation = request_data["operation"]
    selected = request_data.get("task")
    tasks = manifest["tasks"]
    if selected is not None and selected not in tasks:
        raise ValueError("Task is outside this research run")
    result = {}
    for name, task in tasks.items():
        if selected is not None and name != selected:
            continue
        workspace = Path(task["workspace"])
        token = (workspace.parent / "gpuq-token").read_text().strip()

        def query(path: str, body: Any = None, token: str = token) -> Any:
            try:
                return request(path, body, token=token, url=manifest["gpuq_url"], timeout=30)
            except urllib.error.HTTPError as exc:
                error = json.loads(exc.read()).get("error", "request rejected")
                raise RuntimeError(f"GPUQ HTTP {exc.code}: {error}") from exc

        if operation == "submit":
            if selected is None:
                raise ValueError("Submission requires one task")
            source = base64.b64decode(request_data["source"], validate=True)
            if len(source) > 2_000_000:
                raise ValueError("Candidate exceeds 2 MB")
            digest = hashlib.sha256(source).hexdigest()
            key = request_data["key"]
            with (workspace.parent / "submission.lock").open("a+") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                receipts = workspace.parent / "receipts"
                receipts.mkdir(exist_ok=True)
                receipt = receipts / (hashlib.sha256(key.encode()).hexdigest() + ".json")
                intent = {"key": key, "sha256": digest}
                if receipt.exists():
                    if json.loads(receipt.read_text()) != intent:
                        raise ValueError("Submission ID reused with different code")
                else:
                    receipt.write_text(json.dumps(intent))
                old = next((j for j in query("/jobs") if j.get("idempotency_key") == key), None)
                if old is None:
                    candidate = workspace / "model_new.py"
                    candidate.write_bytes(source)
                    old = query(
                        "/jobs",
                        {"kind": "eval", "candidate": "model_new.py", "idempotency_key": key},
                    )
                assert old["task_id"] == task["task_id"] and old["budget"] == 11
                return summarize(old)
        if operation == "job":
            if selected is None:
                raise ValueError("Job lookup requires one task")
            job = query("/jobs/" + request_data["job_id"])
            assert job["task_id"] == task["task_id"]
            return summarize(job)
        jobs = query("/jobs")
        if any(j["task_id"] != task["task_id"] or j["budget"] != 11 for j in jobs):
            raise ValueError("Unexpected task ownership or budget")
        public = [summarize(j) for j in jobs]
        public.sort(key=lambda j: j["created_at"])
        if len(public) > 11:
            raise ValueError("GPU submission cap was exceeded")
        value: dict[str, Any] = {"used": len(public), "remaining": 11 - len(public), "jobs": public}
        if operation == "collect":
            best = max(
                (j for j in public if j["correct"]), key=lambda j: j["speedup"], default=None
            )
            if best:
                raw = next(j for j in jobs if j["id"] == best["id"])
                value["source"] = base64.b64encode(
                    (Path(raw["cwd"]) / "model_new.py").read_bytes()
                ).decode()
                value["best"] = best
        elif operation != "status":
            raise ValueError("Unknown operation")
        result[name] = value
    return result


if __name__ == "__main__":
    try:
        manifest = json.loads(Path(sys.argv[1]).read_text())
        print(json.dumps({"ok": True, "value": handle(manifest, json.load(sys.stdin))}))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        raise SystemExit(1) from exc
