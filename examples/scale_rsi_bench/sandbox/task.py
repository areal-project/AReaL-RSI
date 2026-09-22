#!/usr/bin/env python3
"""Create or resume an RSI run backed by a clean Scale RSI benchmark session."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from session import BENCHMARKS, DEFAULT_RUNTIME_ROOT, create_session

from areal_rsi.api.research import ResearchLimits, TaskBrief
from areal_rsi.base.storage import encode, new_id, safe_name
from areal_rsi.coordination.config import ResearchConfig
from areal_rsi.execution import ApprovedCommand
from areal_rsi.ui.launch import ensure_workbench

HERE = Path(__file__).resolve().parent
JOB = HERE / "job.py"


def build_config(
    benchmark: str,
    session: Path,
    runtime_root: Path,
    *,
    max_agents: int,
    max_jobs: int,
    wall_seconds: float,
) -> ResearchConfig:
    metadata = {
        "template": "scale-rsi-bench",
        "benchmark": benchmark,
        "shared_agent_workspace": True,
        "allow_web_access": False,
        "agent_tool_instructions": (
            "使用分配给自己的 benchmark 工作区；纯 pass@k 下各 Research Agent 的"
            "可写目录彼此独立，其他协作模式可共享工作区；"
            "先读 instruction.md。不要读取或猜测其他运行记录。"
        ),
        "execution_instructions": (
            "本地沙箱没有 GPU，且禁止直接执行 sbatch、srun 或训练入口。"
            "先调用 get_commands，再调用 run_job(command_name='experiment', "
            "parameters_json='{}', wait=true) 提交完整开发实验；"
            "宿主桥会冻结当前工作区并提交 Slurm。作业运行时使用 job_status 或 "
            "wait_for_job 等待原作业，不要重复提交。"
        ),
        "workspace_readonly_mounts": [],
    }
    session_data = json.loads((session / "session.json").read_text())
    metadata["workspace_readonly_mounts"] = session_data["public_mounts"]
    limits = ResearchLimits(
        max_agents=max_agents,
        max_nodes=40,
        max_model_calls=None,
        max_total_tokens=None,
        max_wall_seconds=wall_seconds,
        max_verify_calls=0,
        max_jobs=max_jobs,
    )
    brief = TaskBrief(
        objective=f"完成 {BENCHMARKS[benchmark][1]} benchmark，并取得尽可能好的开发分数",
        instructions=(
            "以工作区 instruction.md 为唯一任务协议。保留可复现代码和最终 submission；"
            f"最多提交 {max_jobs} 次实验。"
        ),
        limits=limits,
    )
    command = ApprovedCommand(
        "experiment",
        (
            sys.executable,
            str(JOB),
            benchmark,
            str(runtime_root),
            "{workspace}",
            "{output}",
        ),
        parameters=("workspace", "output"),
        timeout_seconds=max(wall_seconds, 600),
        public_output_subdir=".",
        feedback_file="summary.json",
    )
    return ResearchConfig(
        brief,
        mode="collaborative",
        seed_dir=str(session / "workspace"),
        commands=(command,),
        metadata=metadata,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("benchmark", choices=BENCHMARKS)
    parser.add_argument("--port", type=int, default=28999)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--run-id")
    parser.add_argument("--resume")
    parser.add_argument("--max-agents", type=int, default=1)
    parser.add_argument("--max-jobs", type=int, default=5)
    parser.add_argument("--hours", type=float, default=3)
    args = parser.parse_args()
    if args.resume and args.run_id:
        parser.error("use --resume or --run-id, not both")
    client, info = ensure_workbench(args.port, args.data_root)
    run_id = (
        safe_name(args.resume or args.run_id)
        if (args.resume or args.run_id)
        else new_id(f"research_{args.benchmark}")
    )
    root = Path(info["data_root"])
    existing = (root / "runs" / run_id / "run.json").exists()
    if not existing:
        session = root / "runs" / run_id / "setup/benchmark-session"
        create_session(args.benchmark, session, args.runtime_root.resolve())
        config = build_config(
            args.benchmark,
            session,
            args.runtime_root.resolve(),
            max_agents=args.max_agents,
            max_jobs=args.max_jobs,
            wall_seconds=args.hours * 3600,
        )
        settings = {
            "base_url": os.environ.get("RSI_BASE_URL", config.base_url),
            "model": os.environ.get("RSI_MODEL", config.model),
            "reasoning_effort": os.environ.get("RSI_REASONING_EFFORT", config.reasoning_effort),
            "backend": os.environ.get("RSI_BACKEND", config.backend),
            "api_key": os.environ.get(config.api_key_env, ""),
        }
        client.request(
            "/api/runs",
            {"run_id": run_id, "config": encode(config), "model_settings": settings},
        )
    detail = client.request("/api/runs/" + run_id)
    if detail["status"] == "planned":
        client.request(f"/api/runs/{run_id}/start", {})
    elif not detail.get("worker_alive") or detail["status"] == "paused":
        client.request(f"/api/runs/{run_id}/resume", {})
    print(f"研究 ID：{run_id}")
    print(f"研究页面：http://127.0.0.1:{args.port}/?run={run_id}")
    print(f"共享工作区：{root / 'runs' / run_id / 'shared-workspace'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
