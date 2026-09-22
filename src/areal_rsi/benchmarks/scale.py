"""Operator-owned Scale RSI benchmark templates for the local workbench."""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from collections.abc import Callable, Collection
from pathlib import Path
from typing import Any

from areal_rsi.api.research import ResearchLimits, TaskBrief
from areal_rsi.coordination.config import ResearchConfig
from areal_rsi.execution import ApprovedCommand

SCALE_BENCHMARKS = {
    "nanogpt": ("nanogpt_env", "nano-gpt-data-curation", "nanoGPT 数据筛选"),
    "opsd": ("opsd_env", "on-policy-self-distillation", "On-Policy Self-Distillation"),
    "jailbreak": ("jailbreak_env", "jailbreak-robustness", "Jailbreak Robustness"),
    "swarm": ("swarm_env", "agent-swarm-optimization", "Agent Swarm Optimization"),
}

WEB_SUBMISSION_NOTE = """

## This deployment's experiment submission

This workspace has no local GPU and cannot call `sbatch`, `srun`, `salloc`, or
`scancel`. Do not probe local CUDA/TRL availability or run the training launcher
directly. Call `get_commands`, then call
`run_job(command_name="experiment", parameters_json="{}", wait=true)`. The host
bridge snapshots this workspace and submits the benchmark's fixed Slurm recipe.
Use `job_status` or `wait_for_job` for that job; do not submit a duplicate.
"""


def runtime_root(repository: Path) -> Path:
    return Path(os.environ.get("RSI_WORK_ROOT", repository.parent / "scale_rsi_bench")).resolve()


def catalog(repository: Path) -> list[dict[str, str]]:
    root = runtime_root(repository)
    return [
        {"id": identifier, "label": label}
        for identifier, (environment, task, label) in SCALE_BENCHMARKS.items()
        if (root / environment / "source/tasks" / task / "instruction.md").is_file()
    ]


def _copy(
    source: Path,
    target: Path,
    *,
    ignore: Callable[[str, list[str]], Collection[str]] | None = None,
) -> None:
    if not source.exists():
        raise FileNotFoundError(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target, dirs_exist_ok=True, symlinks=False, ignore=ignore)
    else:
        shutil.copy2(source, target)


def create_session(benchmark: str, output: Path, root: Path) -> Path:
    if benchmark not in SCALE_BENCHMARKS:
        raise ValueError("未知 benchmark")
    environment, task_name, _ = SCALE_BENCHMARKS[benchmark]
    runtime = (root / environment).resolve()
    task = runtime / "source/tasks" / task_name
    if output.exists() and any(output.iterdir()):
        raise ValueError("benchmark 会话目录不是空目录")
    workspace = output / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _copy(task / "instruction.md", workspace / "instruction.md")
    public_mounts: list[dict[str, str]] = []
    if benchmark == "nanogpt":
        _copy(task / "environment/workspace", workspace)
        (workspace / "data").mkdir(exist_ok=True)
        for name, target in (
            ("pool/pool.jsonl", "data/pool.jsonl"),
            ("pool/pool_meta.npy", "data/pool_meta.npy"),
            ("workspace/data/multi_dev.npy", "data/multi_dev.npy"),
        ):
            source = runtime / name
            if source.exists():
                (workspace / target).touch()
                public_mounts.append({"source": str(source.resolve()), "target": target})
    elif benchmark == "opsd":
        _copy(
            runtime / "workspace/repo",
            workspace / "repo",
            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
        )
        for name in (
            "train_baseline.sh",
            "train_capped.sh",
            "dev_eval.sh",
            "eval_official.py",
        ):
            _copy(task / "environment/workspace" / name, workspace / name)
        _copy(task / "environment/paper.pdf", workspace / "paper.pdf")
        (workspace / "submission").mkdir(exist_ok=True)
        (workspace / "submission/recipe.env").write_text(
            "# Optional whitelisted recipe variables. Empty means the published baseline.\n"
        )
    elif benchmark == "jailbreak":
        for name in ("train_baseline.sh", "dev_eval.sh", "README_data.md"):
            source = task / "environment/workspace" / name
            destination = workspace / ("data/README.md" if name == "README_data.md" else name)
            _copy(source, destination)
        _copy(runtime / "workspace/data", workspace / "data")
        _copy(runtime / "workspace/eval", workspace / "eval")
        (workspace / "submission").mkdir(exist_ok=True)
    else:
        _copy(task / "environment/workspace", workspace)
        (workspace / "submission").mkdir(exist_ok=True)
    instruction = workspace / "instruction.md"
    instruction.write_text(instruction.read_text().rstrip() + WEB_SUBMISSION_NOTE + "\n")
    (output / "session.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "benchmark": benchmark,
                "created_at": int(time.time()),
                "workspace": str(workspace),
                "public_mounts": public_mounts,
            },
            indent=2,
        )
        + "\n"
    )
    return output


def build_config(
    repository: Path,
    benchmark: str,
    session: Path,
    root: Path,
    *,
    max_agents: int,
    max_jobs: int,
    wall_seconds: float,
) -> ResearchConfig:
    if type(max_agents) is not int or not 1 <= max_agents <= 8:
        raise ValueError("Research Agent 数量须为 1 到 8")
    if type(max_jobs) is not int or max_jobs < 1:
        raise ValueError("实验提交次数须为正整数")
    if (
        isinstance(wall_seconds, bool)
        or not isinstance(wall_seconds, (int, float))
        or not 600 <= wall_seconds <= 172800
    ):
        raise ValueError("研究时间须为 10 分钟到 48 小时")
    _, task_name, _ = SCALE_BENCHMARKS[benchmark]
    session_data: dict[str, Any] = json.loads((session / "session.json").read_text())
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
        objective=f"完成 {task_name} benchmark，并取得尽可能好的开发分数",
        instructions=(
            "以工作区 instruction.md 为唯一任务协议。保留可复现代码和最终 submission；"
            f"最多提交 {max_jobs} 次实验。"
        ),
        limits=limits,
    )
    job = repository / "examples/scale_rsi_bench/sandbox/job.py"
    command = ApprovedCommand(
        "experiment",
        (sys.executable, str(job), benchmark, str(root), "{workspace}", "{output}"),
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
        metadata={
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
            "workspace_readonly_mounts": session_data["public_mounts"],
        },
    )


def prepare_config(
    data_root: Path,
    repository: Path,
    run_id: str,
    benchmark: str,
    *,
    max_agents: int = 1,
    max_jobs: int = 5,
    hours: float = 3,
) -> ResearchConfig:
    root = runtime_root(repository)
    if benchmark not in {item["id"] for item in catalog(repository)}:
        raise ValueError("benchmark 运行资产尚未准备好")
    session = data_root / "runs" / run_id / "setup/benchmark-session"
    create_session(benchmark, session, root)
    return build_config(
        repository,
        benchmark,
        session,
        root,
        max_agents=max_agents,
        max_jobs=max_jobs,
        wall_seconds=hours * 3600,
    )
