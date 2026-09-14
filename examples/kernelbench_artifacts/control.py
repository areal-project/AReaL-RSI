"""Operator-side setup and final confirmation, executed inside the et15 container."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

TERMINAL = {"succeeded", "failed", "cancelled", "timed_out", "interrupted"}


def save(path: Path, value: Any) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def prepare(payload: dict[str, Any]) -> dict[str, Any]:
    env, demo = payload["environment"], payload["demo"]
    root = Path(env["kb_root"])
    sys.path.insert(0, str(root))
    from common import request

    health = request("/health", url=env["gpuq_url"], timeout=15)
    if not health.get("ok") or not health.get("gpu_info"):
        raise ValueError("GPUQ 未就绪或没有 GPU")
    if any(gpu["driver_name"] != demo["hardware"] for gpu in health["gpu_info"]):
        raise ValueError("GPU 型号与 demo 固定条件不同")
    protocol = []
    for name, expected in demo["protocol_files"].items():
        digest = hashlib.sha256((root / name).read_bytes()).hexdigest()
        if digest != expected:
            raise ValueError(f"评分器版本不匹配：{name}")
        protocol.append([name, digest])
    references = {}
    for case, info in demo["tasks"].items():
        level, problem = map(int, case[1:].split("P"))
        path = next((Path(env["references"]) / f"level{level}").glob(f"{problem}_*.py"))
        source = path.read_bytes()
        if hashlib.sha256(source).hexdigest() != info["sha256"]:
            raise ValueError(f"题目版本不匹配：{case}")
        references[case] = {"source": base64.b64encode(source).decode(), "path": str(path)}
    if payload["operation"] == "check":
        if not Path(env["admin_token_file"]).is_file():
            raise ValueError("缺少 GPUQ 注册权限，请检查环境配置中的 admin_token_file")
        return {
            "ok": True,
            "gpu_count": len(health["gpu_info"]),
            "queued": health["queued"],
            "tasks": list(references),
            "protocol_matched": True,
        }
    rid = payload["run_id"]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", rid):
        raise ValueError("Invalid run ID")
    base = root / "runs" / rid
    control = base / "control"
    control.mkdir(parents=True, exist_ok=True)
    manifest_path = control / "manifest.json"
    with (control / "setup.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if manifest_path.exists():
            return {"manifest": json.loads(manifest_path.read_text()), "references": references}
        (control / "remote.py").write_text(payload["remote_source"])
        (control / "control.py").write_text(payload["control_source"])
        admin = Path(env["admin_token_file"]).read_text().strip()
        tasks = {}
        for case, ref in references.items():
            level, problem = map(int, case[1:].split("P"))
            workspace = base / case / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            (workspace / "reference.py").write_bytes(base64.b64decode(ref["source"]))
            task_id = "kbfull-v1-codex-full-" + rid + "-" + case
            token_path = workspace.parent / "gpuq-token"
            intent = workspace.parent / "registration.json"
            if not token_path.exists():
                if intent.exists():
                    raise ValueError(
                        f"{case} 的注册回执丢失，请管理员核对该任务，不能另注册以重置额度"
                    )
                save(intent, {"task_id": task_id})
                registered = request(
                    "/tasks",
                    {
                        "id": task_id,
                        "workspace": str(workspace),
                        "level": level,
                        "problem": problem,
                        "budget": demo["exploration_submissions"] + 1,
                    },
                    token=admin,
                    url=env["gpuq_url"],
                    timeout=30,
                )
                with token_path.open("x") as stream:
                    token_path.chmod(0o600)
                    stream.write(registered["token"])
            tasks[case] = {
                "task_id": task_id,
                "workspace": str(workspace),
                "reference": ref["path"],
                "reference_sha256": demo["tasks"][case]["sha256"],
            }
        manifest = {
            **env,
            "run_id": rid,
            "tasks": tasks,
            "remote_script": str(control / "remote.py"),
            "remote_control": str(control / "control.py"),
            "remote_manifest": str(manifest_path),
            "submission_budget_per_task": demo["exploration_submissions"] + 1,
            "exploration_budget_per_task": demo["exploration_submissions"],
            "exploration_seed": demo["exploration_seed"],
            "final_seed": demo["final_seed"],
            "protocol_id": hashlib.sha256(
                json.dumps(protocol, sort_keys=True).encode()
            ).hexdigest(),
            "manifest_id": hashlib.sha256(
                json.dumps(
                    [[case, info["sha256"]] for case, info in demo["tasks"].items()], sort_keys=True
                ).encode()
            ).hexdigest(),
        }
        manifest.pop("admin_token_file", None)
        save(manifest_path, manifest)
        return {"manifest": manifest, "references": references}


def finish(manifest: dict[str, Any]) -> dict[str, Any]:
    """Close exploration before selecting code; replay final submissions by stable key."""
    sys.path.insert(0, manifest["kb_root"])
    from common import request

    control = Path(manifest["remote_manifest"]).parent
    with (control / "closeout.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        (control / "exploration-closed").touch()
        task_jobs = {}
        for case, info in manifest["tasks"].items():
            token = (Path(info["workspace"]).parent / "gpuq-token").read_text().strip()
            jobs = request("/jobs", token=token, url=manifest["gpuq_url"], timeout=30)
            if any(
                j["task_id"] != info["task_id"]
                or j["budget"] != manifest["submission_budget_per_task"]
                for j in jobs
            ):
                raise ValueError("作业归属或额度不匹配")
            if any(j["state"] not in TERMINAL for j in jobs):
                return {"status": "waiting", "stage": "等待已提交 GPU 作业结束"}
            task_jobs[case] = jobs
        path = control / "final-selection.json"
        if not path.exists():
            selected = {}
            for case, jobs in task_jobs.items():
                if len(jobs) > manifest["exploration_budget_per_task"]:
                    raise ValueError("探索提交超额")
                valid = [
                    j
                    for j in jobs
                    if j["kind"] == "eval"
                    and j["state"] == "succeeded"
                    and (j.get("evaluation") or {}).get("correct") is True
                    and j["evaluation"].get("evaluation_policy") == "strict-fp32-v1"
                ]
                if not valid:
                    continue
                best = max(valid, key=lambda j: j["evaluation"]["speedup"])
                source = (Path(best["cwd"]) / "model_new.py").read_bytes()
                selected[case] = {
                    "job_id": best["id"],
                    "sha256": hashlib.sha256(source).hexdigest(),
                    "source": base64.b64encode(source).decode(),
                }
            save(path, selected)
        selected = json.loads(path.read_text())
        receipts = {}
        for case, chosen in selected.items():
            info = manifest["tasks"][case]
            workspace = Path(info["workspace"])
            token = (workspace.parent / "gpuq-token").read_text().strip()
            with (workspace.parent / "submission.lock").open("a+") as task_lock:
                fcntl.flock(task_lock, fcntl.LOCK_EX)
                jobs = task_jobs[case]
                key = manifest["run_id"] + "-" + case + "-final"
                job = next((j for j in jobs if j.get("idempotency_key") == key), None)
                if job is None:
                    if len(jobs) >= manifest["submission_budget_per_task"]:
                        raise ValueError("没有剩余最终复测额度")
                    source = base64.b64decode(chosen["source"], validate=True)
                    if hashlib.sha256(source).hexdigest() != chosen["sha256"]:
                        raise ValueError("冻结源码摘要不匹配")
                    (workspace / "model_new.py").write_bytes(source)
                    (workspace / "_rsi_diagnostic.py").unlink(missing_ok=True)
                    job = request(
                        "/jobs",
                        {
                            "kind": "eval",
                            "candidate": "model_new.py",
                            "seed": manifest["final_seed"],
                            "idempotency_key": key,
                            "timeout_s": 300,
                        },
                        token=token,
                        url=manifest["gpuq_url"],
                        timeout=30,
                    )
                if (
                    hashlib.sha256((Path(job["cwd"]) / "model_new.py").read_bytes()).hexdigest()
                    != chosen["sha256"]
                ):
                    raise ValueError("最终作业源码不匹配")
                receipts[case] = {
                    "id": job["id"],
                    "state": job["state"],
                    "sha256": chosen["sha256"],
                }
        done = all(value["state"] in TERMINAL for value in receipts.values())
        return {
            "status": "completed" if done else "waiting",
            "stage": "最终独立复测",
            "selected": selected,
            "receipts": receipts,
        }


if __name__ == "__main__":
    try:
        data = json.load(sys.stdin)
        value = finish(data["manifest"]) if data["operation"] == "finish" else prepare(data)
        print(json.dumps({"ok": True, "value": value}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        raise SystemExit(1) from exc
