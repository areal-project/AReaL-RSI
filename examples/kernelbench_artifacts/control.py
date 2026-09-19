"""Operator-side setup and final confirmation, executed inside the et15 container."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import math
import re
import runpy
import sys
from pathlib import Path
from typing import Any

TERMINAL = {"succeeded", "failed", "cancelled", "timed_out", "interrupted"}
REQUIRED_JOB_ISOLATION = "filesystem-network-v1"


def require_job_isolation(health: dict[str, Any]) -> None:
    """Require an operator-enforced boundary before submitting any model code.

    The queue must isolate each job's files, processes and network before loading
    candidates or diagnostics. GPU device masking/private TMPDIR alone is not this contract.
    """
    if health.get("job_isolation") != REQUIRED_JOB_ISOLATION:
        raise ValueError(
            "GPUQ 缺少 filesystem-network-v1 作业隔离，拒绝启动研究。"
            "候选与诊断都必须在无法读取其他作业/run、凭据或队列接口的环境执行；"
            "仅隔离 GPU 和临时目录不够。请先升级队列隔离，不能只添加能力标记。"
        )


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
    # Existing GPUQ processes load this entrypoint for every job. Verify the
    # installed implementation without restarting the shared scheduler.
    expected_sandbox = payload.get("queue_sandbox_sha256")
    sandbox = root / "sandbox_exec.py"
    if expected_sandbox and hashlib.sha256(sandbox.read_bytes()).hexdigest() == expected_sandbox:
        health["job_isolation"] = REQUIRED_JOB_ISOLATION
    require_job_isolation(health)
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
        digest = hashlib.sha256(source).hexdigest()
        if info.get("sha256") and digest != info["sha256"]:
            raise ValueError(f"题目版本不匹配：{case}")
        info["sha256"] = digest
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
            "job_isolation": health["job_isolation"],
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
            manifest = json.loads(manifest_path.read_text())
            require_job_isolation(manifest)
            return {"manifest": manifest, "references": references}
        (control / "remote.py").write_text(payload["remote_source"])
        (control / "control.py").write_text(payload["control_source"])
        (control / "scoring.py").write_text(payload["scoring_source"])
        admin = Path(env["admin_token_file"]).read_text().strip()
        exploration_budget = demo["exploration_submissions"]
        budget = exploration_budget + 1 if exploration_budget is not None else None
        auth = control / "queue-auth.json"
        save(auth, {"token_file": env["admin_token_file"]})
        auth.chmod(0o600)
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
                        "budget": budget,
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
            "job_isolation": health["job_isolation"],
            "tasks": tasks,
            "remote_script": str(control / "remote.py"),
            "remote_control": str(control / "control.py"),
            "remote_manifest": str(manifest_path),
            "submission_budget_per_task": budget,
            "exploration_budget_per_task": demo["exploration_submissions"],
            "exploration_seed": demo["exploration_seed"],
            "metric": demo.get("metric", "fast_1"),
            "final_seed": demo["final_seed"],
            "search_scoring": demo["search_scoring"],
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


def calibrate(manifest: dict[str, Any]) -> dict[str, Any]:
    """Poll a fixed number of preparation jobs, outside the research submission budget."""
    from scoring import calibration

    sys.path.insert(0, manifest["kb_root"])
    from common import request

    control = Path(manifest["remote_manifest"]).parent
    policy = manifest["search_scoring"]
    count = policy["calibration_runs"]
    maximum = policy["max_calibration_runs"]
    if type(count) is not int or count < 3 or type(maximum) is not int or maximum < count:
        raise ValueError("Reference 校准至少需要三个独立批次，且不能超过校准上限")
    with (control / "calibration.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        frozen = json.loads(Path(manifest["remote_manifest"]).read_text())
        if all("reference_calibration" in info for info in frozen["tasks"].values()):
            return {"status": "completed", "manifest": frozen}
        auth = json.loads((control / "queue-auth.json").read_text())
        token = Path(auth["token_file"]).read_text().strip()
        path = control / "reference-jobs.json"
        receipts = json.loads(path.read_text()) if path.exists() else {}
        waiting = False
        results = {}
        for case, info in manifest["tasks"].items():
            jobs = []
            for index in range(maximum):
                key = f"{manifest['run_id']}-{case}-reference-{index}"
                if key not in receipts:
                    body = {
                        "kind": "baseline",
                        "task_id": info["task_id"] + "-reference",
                        "cwd": info["workspace"],
                        "level": int(case[1]),
                        "problem": int(case.split("P")[1]),
                        "gpus": 1,
                        "seed": manifest["exploration_seed"],
                        "timeout_s": 300,
                        "idempotency_key": key,
                    }
                    job = request("/jobs", body, token=token, url=manifest["gpuq_url"], timeout=30)
                    receipts[key] = job["id"]
                    save(path, receipts)
                job = request(
                    "/jobs/" + receipts[key], token=token, url=manifest["gpuq_url"], timeout=30
                )
                if job["task_id"] != info["task_id"] + "-reference" or job["kind"] != "baseline":
                    raise ValueError("Reference 校准作业归属不匹配")
                jobs.append(job)
                if len(jobs) >= count:
                    if any(job["state"] not in TERMINAL for job in jobs):
                        waiting = True
                        break
                    if reference := calibration(jobs, policy):
                        results[case] = reference
                        break
            else:
                raise ValueError(f"{case} 在 {maximum} 批内未得到稳定 reference；请检查评测环境")
        if waiting:
            return {"status": "waiting"}
        for case, reference in results.items():
            frozen["tasks"][case]["reference_calibration"] = reference
        # Scoring and its frozen denominator are part of the task's verification identity.
        frozen["protocol_id"] = hashlib.sha256(
            json.dumps(
                {
                    "evaluator": manifest["protocol_id"],
                    "search_scoring": policy,
                    "references": results,
                    "scoring_sha256": hashlib.sha256(
                        (control / "scoring.py").read_bytes()
                    ).hexdigest(),
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        save(Path(manifest["remote_manifest"]), frozen)
        return {"status": "completed", "manifest": frozen}


def select_candidate(
    manifest: dict[str, Any], case: str, jobs: list[dict[str, Any]], excluded: set[str]
) -> dict[str, Any] | None:
    """Select the highest existing search score; never retest the candidate pool."""
    from scoring import evaluation

    ranked = []
    for job in jobs:
        value = evaluation(manifest, case, job["evaluation"])
        score = value.get("speedup", 0)
        if not isinstance(score, (int, float)) or not math.isfinite(score) or score <= 0:
            continue
        ranked.append((score, job))
    for score, job in sorted(ranked, key=lambda pair: pair[0], reverse=True):
        source = (Path(job["cwd"]) / "model_new.py").read_bytes()
        digest = hashlib.sha256(source).hexdigest()
        if digest in excluded:
            continue
        return {
            "job_id": job["id"],
            "sha256": digest,
            "source": base64.b64encode(source).decode(),
            "selection_method": "best-search-score-v1",
            "search_score": score,
        }
    return None


def finish(manifest: dict[str, Any]) -> dict[str, Any]:
    """Close exploration before selecting code; replay final submissions by stable key."""
    budget = manifest["submission_budget_per_task"]
    exploration_budget = manifest["exploration_budget_per_task"]
    if budget is None:
        request = runpy.run_path(manifest["remote_script"])["task_request"]
    else:
        # Existing bounded runs keep their frozen bridge, which predates task_request.
        sys.path.insert(0, manifest["kb_root"])
        from common import request as queue_request

        def request(manifest: dict[str, Any], case: str, path: str, body: Any = None) -> Any:
            token = Path(manifest["tasks"][case]["workspace"]).parent / "gpuq-token"
            return queue_request(
                path, body, token=token.read_text().strip(), url=manifest["gpuq_url"], timeout=30
            )

    control = Path(manifest["remote_manifest"]).parent
    with (control / "closeout.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        (control / "exploration-closed").touch()
        task_jobs = {}
        for case, info in manifest["tasks"].items():
            jobs = request(manifest, case, "/jobs")
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
                if exploration_budget is not None and len(jobs) > exploration_budget:
                    raise ValueError("探索提交超额")
                valid = [
                    j
                    for j in jobs
                    if j["kind"] == "eval"
                    and j["state"] == "succeeded"
                    and (j.get("evaluation") or {}).get("correct") is True
                    and j["evaluation"].get("evaluation_policy") == "strict-fp32-v1"
                    and (
                        "exploration_job_cutoffs" not in manifest
                        or (j.get("finished_at") or float("inf"))
                        <= manifest["exploration_job_cutoffs"].get(j["id"], 0)
                    )
                    and (
                        manifest.get("exploration_finished_at") is None
                        or (j.get("finished_at") or float("inf"))
                        <= manifest["exploration_finished_at"]
                    )
                ]
                if not valid:
                    continue
                excluded = set(manifest.get("excluded_source_sha256", {}).get(case, []))
                if chosen := select_candidate(manifest, case, valid, excluded):
                    selected[case] = chosen
            save(path, selected)
        selected = json.loads(path.read_text())
        receipts = {}
        for case, chosen in selected.items():
            if chosen["sha256"] in manifest.get("excluded_source_sha256", {}).get(case, []):
                raise ValueError("冻结的最终产物已被撤回，不能继续交付")
            info = manifest["tasks"][case]
            workspace = Path(info["workspace"])
            with (workspace.parent / "submission.lock").open("a+") as task_lock:
                fcntl.flock(task_lock, fcntl.LOCK_EX)
                jobs = task_jobs[case]
                key = manifest["run_id"] + "-" + case + "-final"
                job = next((j for j in jobs if j.get("idempotency_key") == key), None)
                if job is None:
                    if budget is not None and len(jobs) >= budget:
                        raise ValueError("没有剩余最终复测额度")
                    source = base64.b64decode(chosen["source"], validate=True)
                    if hashlib.sha256(source).hexdigest() != chosen["sha256"]:
                        raise ValueError("冻结源码摘要不匹配")
                    (workspace / "model_new.py").write_bytes(source)
                    (workspace / "_rsi_diagnostic.py").unlink(missing_ok=True)
                    job = request(
                        manifest,
                        case,
                        "/jobs",
                        {
                            "kind": "eval",
                            "candidate": "model_new.py",
                            "seed": manifest["final_seed"],
                            "idempotency_key": key,
                            "timeout_s": 300,
                        },
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
        if data["operation"] == "finish":
            value = finish(data["manifest"])
        elif data["operation"] == "calibrate":
            value = calibrate(data["manifest"])
        else:
            value = prepare(data)
        print(json.dumps({"ok": True, "value": value}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        raise SystemExit(1) from exc
