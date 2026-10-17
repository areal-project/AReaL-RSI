"""Run a real /rsi Interaction -> Leader session for a prepared TIR study."""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from areal_rsi.api.control import encode_record
from areal_rsi.api.research import SessionPhase, SessionStatus
from areal_rsi.infra.artifacts import read_record, write_record
from areal_rsi.infra.config import executable, from_environment
from areal_rsi.infra.research import ResearchSystem
from examples.tir.study import PROJECT, Study


@dataclass(slots=True)
class DriverReceipt:
    run_id: str
    system_root: str
    study: str
    entered_leader: bool = False


@dataclass(frozen=True, slots=True)
class ResearchDecision:
    candidate: str
    rationale: str


def brief(study: Path) -> str:
    python = PROJECT / ".venv/bin/python"
    command = f"{python} -m examples.tir.study --study {study}"
    return f"""用户明确授权现在开始执行真实 TIR 多轮研究与训练。先保存任务，然后交接 Leader。
目标：在冻结的数学训练/dev/最终评测协议下，通过改进数据合成代码、数据配比/难度课程和 GRPO 训练配方，
让模型经过多轮迭代后有提升。公共执行/训练/评测服务已在 infra，研究策略在 examples/tir。

工作区：{PROJECT}。冻结实验：{study}。先读取 examples/tir/RESEARCH.md、protocol.json、
dev/base.json 和 dev 基线轨迹，检查冻结条件。用户要求数据合成代码和 pipeline 认真设计。
模型为 Qwen2.5-Math-1.5B；后端使用 /storage/openpsi/users/fenghui/projects/AReaL；
GPU 使用 rsi reservation，避免改动任何其他用户的作业。已有阶段会持久化记录；不要重复提交。

研发权限：可修改 examples/tir/synthesis.py 和对应生成器测试，
可新增/修改训练 recipe JSON 与分析/研究报告。
examples/tir/study.py、benchmark.py、worker.py、workflow.py、reward.py、pipeline.py、
src/、AReaL 后端、protocol/freeze/harness/benchmark 数据与已有结果已冻结，禁止修改。
禁止读取 official test、heldout/ 数据、final/ 结果。只能根据 dev 反馈调整策略；
真实训练池位于 protocol.benchmark.train，只能从该池读取训练题/参考解。禁止将 dev 题目
或答案直接/改写后放进训练；可用 dev 的失败模式设计独立可验证的训练题。
这是同一用户的研究约束，文件哈希会检查，但不是操作系统级隔离。

阶段命令（从工作区运行，不要同时改源码与运行阶段）：
  {command} status
  {command} round --name r1 --recipe /absolute/path/to/recipe.json
  {command} evaluate --candidate r1  # 仅用于已训练但 dev 阶段中断的候选
recipe 按 examples.tir.study.RoundRecipe 解码，写明 hypothesis、parent、data 和训练参数；
本次 ToRL 数据参考 examples/tir/recipes/torl-r1.json。每轮新建名字，
使用上轮 checkpoint warm start 或明确分支；
优化器每轮重新初始化，不能声称恢复了优化器状态。每轮只有最后一步保存 checkpoint。
训练池是官方 ToRL 数学题，答案包括分数、根式等，评分采用 AReaL 原生 math_verify。
原整数文字题合成器只是起点，不能直接假定它适合竞赛数学。请依据训练/dev 的真实错误模式
设计合成、筛选和课程；新题需要精确数学 oracle 和独立题面验算。不得从 dev 改写出训练题。
ToRL 训练数据没有完整参考推导，real_min_steps 应为0；可以研发有证据的训练池难题筛选。
测试集有同题重复采样，独立置信区间由宿主按题目聚类计算；禁止将重复行当成独立题目。

完成要求：至少 2 轮真实训练和 dev 评测，建议先做 32 步，分析失败轨迹后修改 synthesis.py
或数据策略再做下一轮。不是机械续训，也不能只调整 harness 或增加评测推理预算。
记录每轮假设、代码/配方变化、dev 效果、增益与退化案例、成本，并解释下一轮选择依据。
争取 dev 相对基线至少 +5 个百分点以获得独立最终集上的稳定收益；如未改善则继续研发。
可先做训练数据中小规模 rollout 分析筛选，禁止使用 final。运行有意义的合成验证测试。
比较全 study 已完成候选，包括宿主 smoke/bootstrap；这些不计作你的至少两轮真实研发。
如果最佳仍是宿主候选，应继续研发。不能只在两个退化候选中选一个并宣称改进。
选择 dev 最好的已训练候选，写 {study}/decision.json，格式为
{{"candidate": "候选名", "rationale": "选择依据"}}，并写 {study}/research-report.md。
然后通过 rsi_update_goal 完成这个 dev 研发子目标。宿主会冻结选择，再独立做最终评测；
最终集目标约 +3 个百分点，不能提前声称已达到，也不要自行运行 finalize。
"""


async def run(study: Path, env_file: Path | None, *, resume: bool) -> None:
    if env_file is not None:
        load_dotenv(env_file, override=False)
    config = from_environment(PROJECT)
    config.auto_learn = False
    executable(config)  # Fail before creating a misleading empty research session.
    current = Study(study)
    if not (study / "dev/base.json").exists():
        raise ValueError("Run the frozen dev baseline before handing research to RSI")
    root = study / "rsi"
    receipt_path = root / "driver.json"
    system = ResearchSystem(root)
    if receipt_path.exists():
        if not resume:
            raise ValueError("A research session already exists; use --resume")
        receipt = read_record(receipt_path, DriverReceipt)
    else:
        if resume:
            raise ValueError("No existing research session to resume")
        receipt = DriverReceipt(await system.create(config), str(root), str(study))
        write_record(receipt_path, receipt)

    async def log_events() -> None:
        with (root / "events.jsonl").open("a") as stream:
            async for event in system.events(receipt.run_id):
                stream.write(json.dumps(encode_record(event), ensure_ascii=False) + "\n")
                stream.flush()
                if event.kind in {"message", "tool", "error", "prompt"}:
                    print(f"[{event.kind}:{event.role}] {event.text}", flush=True)

    try:
        await system.open(receipt.run_id)
        events = asyncio.create_task(log_events())
        if resume:
            await system.resume(receipt.run_id)
        else:
            await system.enter_rsi(receipt.run_id, brief(study))
        while True:
            record = system.detail(receipt.run_id)
            if record.phase == SessionPhase.LEADER and not receipt.entered_leader:
                receipt.entered_leader = True
                write_record(receipt_path, receipt)
                print(
                    f"real Leader entered: run={receipt.run_id} thread={record.thread_id}",
                    flush=True,
                )
            if (
                record.status
                in {
                    SessionStatus.COMPLETED,
                    SessionStatus.FAILED,
                    SessionStatus.STOPPED,
                    SessionStatus.BUDGET_LIMITED,
                    SessionStatus.WAITING,
                }
                or record.progress.decision == "blocked"
            ):
                break
            if events.done():
                await events
            await asyncio.sleep(1)
        events.cancel()
        await asyncio.gather(events, return_exceptions=True)
        write_record(root / "result.json", record)
        if record.status != SessionStatus.COMPLETED or not receipt.entered_leader:
            raise RuntimeError(
                f"RSI requires attention: {record.status}; {record.last_error}; "
                f"{record.progress.question}"
            )
        decision = read_record(study / "decision.json", ResearchDecision)
        candidate = current.candidate(decision.candidate)
        if candidate.status != "completed" or candidate.dev is None:
            raise ValueError("Research decision requires a completed, evaluated candidate")
        # The host reviews dev evidence before independently freezing selection.
        print(
            f"RSI dev research completed; proposed candidate={decision.candidate}. Final is unrun."
        )
    finally:
        await system.close()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    task = asyncio.create_task(run(args.study.resolve(), args.env_file, resume=args.resume))
    for signum in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(signum, task.cancel)
    await task


if __name__ == "__main__":
    asyncio.run(main())
