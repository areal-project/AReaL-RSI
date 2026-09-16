"""TUI entry to operator-configured research; execution stays in the workbench."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from areal_rsi.base.storage import safe_name, write_json
from areal_rsi.coordination.config import ResearchConfig, load_config
from areal_rsi.coordination.intake import validate_execution
from areal_rsi.ui.launch import REPOSITORY, ensure_workbench


def prepared_config() -> ResearchConfig:
    """Preflight fixed operator inputs before starting any model or evaluation."""
    from areal_rsi.adapters.past_bench import PHASES, load_lock, phase_identity

    path = (
        Path(
            os.environ.get(
                "RSI_PAST_BENCH_CONFIG", str(REPOSITORY / ".rsi/past-bench/research-current.json")
            )
        )
        .expanduser()
        .resolve()
    )
    if not path.is_file():
        raise ValueError(
            "尚未配置 PAST-Bench，请按 examples/past_bench/README.md 完成 prepare/configure"
        )
    config = load_config(path)
    issues = validate_execution(config)
    if issues:
        raise ValueError("PAST-Bench 研究配置未通过执行检查")
    if config.metadata.get("template") != "past-bench" or config.verifier is None:
        raise ValueError("请选择 PAST-Bench 的研究配置")
    command = config.verifier.command
    if "--lock" not in command:
        raise ValueError("PAST-Bench 配置缺少协议锁")
    lock = load_lock(Path(command[command.index("--lock") + 1]))
    expected = {
        "adapter": "harness",
        "phases": {phase: phase_identity(lock, phase) for phase in PHASES},
    }
    if config.metadata.get("verification_identity") != expected:
        raise ValueError("PAST-Bench 配置与协议锁不一致，请重新 configure")
    if not config.api_key():
        raise ValueError("研究模型凭证未配置，请检查研究配置的 api_key_env")
    for key in ("api_key_env", "judge_api_key_env"):
        if not os.environ.get(lock["protocol"]["options"][key]):
            raise ValueError("Task Agent 或评分模型凭证未配置，请检查 operator 中的环境变量")
    config.pin_model_settings()
    return config


def summary(detail: dict[str, Any], url: str) -> dict[str, Any]:
    """Never return private traces, credentials, tool output or evaluator configuration."""
    result = {
        key: detail.get(key)
        for key in (
            "run_id",
            "status",
            "simulated",
            "objective",
            "worker_alive",
            "budget",
            "budget_stop_reason",
        )
    }
    for key in ("baseline", "best", "final"):
        score = detail.get(key)
        result[key] = (
            {
                name: score.get(name)
                for name in (
                    "score",
                    "metrics",
                    "passed",
                    "status",
                    "phase",
                    "candidate_id",
                )
            }
            if isinstance(score, dict)
            else None
        )
    result["jobs"] = [
        {
            key: job.get(key)
            for key in (
                "job_id",
                "status",
                "phase",
                "is_baseline",
            )
        }
        for job in detail.get("jobs", [])[-10:]
    ]
    result["report_url"] = url + "/?run=" + detail["run_id"]
    return result


class ResearchBridge:
    def __init__(self, root: Path):
        self.root = root.resolve() / "past-bench-research"
        self.port = int(os.environ.get("RSI_RESEARCH_PORT", "8198"))

    def start(self, goal: str, request_id: str) -> dict[str, Any]:
        safe_name(request_id)
        if not goal.strip() or len(goal) > 4000:
            raise ValueError("请提供研究目标")
        # The same user request is retryable after a disconnected tool response.
        # Store its exact keyless payload so a retry cannot create another run.
        run_id = "research_past_" + hashlib.sha256(request_id.encode()).hexdigest()[:24]
        directory = self.root / "tui-requests"
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / (run_id + ".lock")).open("a") as guard:
            fcntl.flock(guard, fcntl.LOCK_EX)
            record = directory / (run_id + ".json")
            if record.exists():
                payload = json.loads(record.read_text())
                if payload["config"]["brief"]["objective"] != goal:
                    raise ValueError("该请求编号已用于其他目标，请为新请求使用新的编号")
            else:
                config = prepared_config()
                config.brief.objective = goal
                payload = {"run_id": run_id, "config": config.public()}
                write_json(record, payload, immutable=True)
            client, _ = ensure_workbench(self.port, self.root)
            detail = client.request("/api/runs", payload)
            if detail["status"] == "planned":
                client.request(f"/api/runs/{run_id}/start", {})
            detail = client.request(f"/api/runs/{run_id}")
            return summary(detail, client.url)

    def control(self, run_id: str, action: str = "status") -> dict[str, Any]:
        safe_name(run_id)
        if action not in {"status", "pause", "resume", "stop"}:
            raise ValueError("不支持的研究操作")
        client, _ = ensure_workbench(self.port, self.root)
        path = "/api/runs/" + run_id
        if action != "status":
            client.request(path + "/" + action, {})
        detail = client.request(path)
        return summary(detail, client.url)
