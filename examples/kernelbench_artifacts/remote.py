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


def summarize(job: dict[str, Any], external_used: int = 0) -> dict[str, Any]:
    evaluation = job.get("evaluation") or {}
    speedup = evaluation.get("speedup", 0)
    correct = (
        job["kind"] == "eval" and evaluation.get("correct") is True and job["state"] == "succeeded"
    )
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
        "budget_used": job["budget_used"] + external_used,
        "queue_budget_used": job["budget_used"],
        "external_budget_used": external_used,
        "source_sha256": digest,
        **(
            {
                "diagnostic_sha256": hashlib.sha256(
                    (Path(job["cwd"]) / "_rsi_diagnostic.py").read_bytes()
                ).hexdigest()
                if (Path(job["cwd"]) / "_rsi_diagnostic.py").is_file()
                else None,
                "stdout_tail": str(job.get("stdout_tail", ""))[-12000:],
            }
            if job["kind"] == "run"
            else {}
        ),
        "correct": correct,
        "speedup": float(speedup) if correct and valid else 0.0,
        "evaluation": evaluation,
        "stderr_tail": str(job.get("stderr_tail", ""))[-12000:],
    }


def diagnostic_logs(job: dict[str, Any]) -> dict[str, Any]:
    if job["kind"] != "run":
        raise ValueError("Diagnostic logs require a diagnostic job")
    result = {}
    for stream in ("stdout", "stderr"):
        path = Path(job.get(stream + "_path", "")).resolve()
        artifacts = Path(job["artifacts"]).resolve()
        if path.is_relative_to(artifacts) and path.is_file():
            with path.open("rb") as handle:
                content = handle.read(1_000_001)
            result[stream] = {
                "text": content[:1_000_000].decode(errors="replace"),
                "truncated": len(content) > 1_000_000,
                "source": "file",
            }
        else:
            result[stream] = {
                "text": str(job.get(stream + "_tail", "")),
                "truncated": True,
                "source": "queue_tail",
            }
    return result


def external_submissions(workspace: Path, task_id: str) -> list[dict[str, Any]]:
    """Operator-audited GPU work outside the queue still consumes the task budget."""
    records = []
    for path in sorted((workspace.parent / "external-submissions").glob("*.json")):
        record = json.loads(path.read_text())
        if (
            record.get("task_id") != task_id
            or record.get("event_id") != path.stem
            or type(record.get("count")) is not int
            or record["count"] != 1
        ):
            raise ValueError("Invalid external GPU submission record")
        records.append(record)
    return records


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
        external = external_submissions(workspace, task["task_id"])
        token = (workspace.parent / "gpuq-token").read_text().strip()

        def query(path: str, body: Any = None, token: str = token) -> Any:
            try:
                return request(path, body, token=token, url=manifest["gpuq_url"], timeout=30)
            except urllib.error.HTTPError as exc:
                error = json.loads(exc.read()).get("error", "request rejected")
                raise RuntimeError(f"GPUQ HTTP {exc.code}: {error}") from exc

        if operation in {"submit", "submit_diagnostic"}:
            if selected is None:
                raise ValueError("Submission requires one task")
            diagnostic = operation == "submit_diagnostic"
            source = base64.b64decode(request_data["source"], validate=True)
            script = base64.b64decode(request_data["script"], validate=True) if diagnostic else b""
            if len(source) + len(script) > 2_000_000:
                raise ValueError("Submission sources exceed 2 MB")
            if diagnostic:
                compile(script, "_rsi_diagnostic.py", "exec")
                if not script.strip():
                    raise ValueError("Diagnostic script is empty")
            digest = hashlib.sha256(source).hexdigest()
            key = request_data["key"]
            with (workspace.parent / "submission.lock").open("a+") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                external = external_submissions(workspace, task["task_id"])
                receipts = workspace.parent / "receipts"
                receipts.mkdir(exist_ok=True)
                receipt = receipts / (hashlib.sha256(key.encode()).hexdigest() + ".json")
                intent = {"key": key, "sha256": digest}
                if diagnostic:
                    intent.update(kind="run", script_sha256=hashlib.sha256(script).hexdigest())
                if receipt.exists():
                    if json.loads(receipt.read_text()) != intent:
                        raise ValueError("Submission ID reused with different code")
                else:
                    receipt.write_text(json.dumps(intent))
                jobs = query("/jobs")
                old = next((j for j in jobs if j.get("idempotency_key") == key), None)
                if old is None:
                    if len(jobs) + len(external) >= 11:
                        raise ValueError(
                            "GPU feedback budget exhausted (including external GPU work)"
                        )
                    candidate = workspace / "model_new.py"
                    diagnostic_path = workspace / "_rsi_diagnostic.py"
                    if diagnostic:
                        diagnostic_path.write_bytes(script)
                        if source:
                            candidate.write_bytes(source)
                        else:
                            candidate.unlink(missing_ok=True)
                        body = {
                            "kind": "run",
                            "argv": [sys.executable, "-u", "_rsi_diagnostic.py"],
                            "gpus": 1,
                            "timeout_s": 300,
                            "idempotency_key": key,
                        }
                    else:
                        diagnostic_path.unlink(missing_ok=True)
                        candidate.write_bytes(source)
                        body = {
                            "kind": "eval",
                            "candidate": "model_new.py",
                            "idempotency_key": key,
                        }
                    old = query("/jobs", body)
                assert old["task_id"] == task["task_id"] and old["budget"] == 11
                if old["kind"] != ("run" if diagnostic else "eval"):
                    raise ValueError("Submission ID reused for another operation")
                return summarize(old, len(external))
        if operation in {"job", "logs"}:
            if selected is None:
                raise ValueError("Job lookup requires one task")
            job = query("/jobs/" + request_data["job_id"])
            assert job["task_id"] == task["task_id"] and job["budget"] == 11
            return diagnostic_logs(job) if operation == "logs" else summarize(job, len(external))
        jobs = query("/jobs")
        if any(j["task_id"] != task["task_id"] or j["budget"] != 11 for j in jobs):
            raise ValueError("Unexpected task ownership or budget")
        public = [summarize(j, len(external)) for j in jobs]
        public.sort(key=lambda j: j["created_at"])
        if len(public) > 11:
            raise ValueError("GPU submission cap was exceeded")
        used = len(public) + len(external)
        value: dict[str, Any] = {
            "used": used,
            "remaining": max(0, 11 - used),
            "queue_used": len(public),
            "external_used": len(external),
            "external_submissions": external,
            "jobs": public,
        }
        if operation == "collect":
            best = max(
                (j for j in public if j["kind"] == "eval" and j["correct"]),
                key=lambda j: j["speedup"],
                default=None,
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
