"""Start or explicitly resume the search research goal in native ResearchSystem."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from dotenv import load_dotenv

from areal_rsi.api.research import SessionStatus
from areal_rsi.infra.artifacts import read_record, write_record
from areal_rsi.infra.config import from_environment
from areal_rsi.infra.research import ResearchSystem


@dataclass(frozen=True, slots=True)
class GoalSession:
    run_id: str
    workspace: str
    objective: str


async def run(root: Path, workspace: Path, objective: Path, *, resume: bool) -> None:
    root.mkdir(parents=True, exist_ok=True)
    with (root / "driver.lock").open("a") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        system = ResearchSystem(root / "system")
        receipt_path = root / "session.json"
        ownership = (
            "你就是当前原生 RSI 执行会话，控制进程位于 Slurm Job "
            f"{os.environ.get('SLURM_JOB_ID', 'local')}。"
            "这个 Job 运行你自己，不是等待产出的训练实验。不要等待它结束、取消它、杀死父进程、"
            "递归启动 examples.search.goal，或修改当前控制进程的 Python 环境。"
            "你负责亲自审查训练数据、选择配置、调用 examples.search.experiment.pipeline --submit "
            "提交 GPU 实验。"
            "只对提交回执中记录的实际 GPU 实验作业执行等待和恢复。"
            "不要运行 uv sync/uv run 重建共享 .venv；使用当前已提供的 Python 和已安装依赖。"
            "execution-ready.json 是执行授权与冻结运行时的唯一入口；不存在时可分析数据和策略，"
            "不能根据旧的 execution-ready-v*.json 启动作业。主目标和原验收条款全部保留。\n"
            "之前把自己的控制 Job 当作外部实验等待是协调错误，不能延续那个 next_step。"
            "从 execution-ready.json、外部数据质量报告和真实 GPU 实验产物继续实际执行。\n"
        )
        steer_pending = resume
        try:
            if receipt_path.exists():
                if not resume:
                    raise ValueError("A native session exists; inspect it and use --resume")
                receipt = read_record(receipt_path, GoalSession)
                if receipt.workspace != str(workspace) or receipt.objective != str(objective):
                    raise ValueError("Resume must retain the same workspace and objective")
                await system.open(receipt.run_id)
                await system.resume(receipt.run_id)
            else:
                config = from_environment(workspace)
                config.keep_awake = False
                receipt = GoalSession(await system.create(config), str(workspace), str(objective))
                write_record(receipt_path, receipt)
                await system.enter_rsi(
                    receipt.run_id,
                    "用户已明确授权立即执行以下完整研究目标。保存任务并交接执行 Leader，"
                    "不需要重新询问授权。保留所有验收条款，不得缩小目标。\n\n"
                    + ownership
                    + objective.read_text(),
                )
            print(f"Native ResearchSystem session: {receipt.run_id}", flush=True)
            async for event in system.events(receipt.run_id):
                with (root / "events.jsonl").open("a") as output:
                    output.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")
                if event.kind in {"message", "status", "error", "prompt"}:
                    print(json.dumps(asdict(event), ensure_ascii=False), flush=True)
                record = system.detail(receipt.run_id)
                if steer_pending and record.active_turn_id:
                    await system.send_message(receipt.run_id, ownership)
                    steer_pending = False
                if record.status in {
                    SessionStatus.COMPLETED,
                    SessionStatus.STOPPED,
                    SessionStatus.FAILED,
                    SessionStatus.BUDGET_LIMITED,
                }:
                    write_record(root / "last-state.json", record)
                    return
        finally:
            await system.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--objective", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    arguments = parser.parse_args()
    load_dotenv(arguments.env_file, override=True)
    asyncio.run(
        run(
            arguments.root.resolve(),
            arguments.workspace.resolve(),
            arguments.objective.resolve(),
            resume=arguments.resume,
        )
    )


if __name__ == "__main__":
    main()
