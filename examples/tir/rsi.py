"""Run a real /rsi Interaction -> Leader session for a prepared TIR experiment."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import shlex
import signal
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

from areal_rsi.api.control import encode_record
from areal_rsi.api.execution import JobSpec
from areal_rsi.api.research import SessionPhase, SessionStatus
from areal_rsi.infra.artifacts import read_record, write_record
from areal_rsi.infra.config import executable, from_environment
from areal_rsi.infra.execution import JobExecutor
from areal_rsi.infra.research import ResearchSystem
from examples.tir.experiment import Experiment, ExperimentConfig, prepare
from examples.tir.pipeline import PROJECT


@dataclass(slots=True)
class DriverReceipt:
    run_id: str
    system_root: str
    experiment: str
    entered_leader: bool = False


@dataclass(frozen=True, slots=True)
class ResearchDecision:
    candidate: str
    rationale: str


def brief(current: Experiment) -> str:
    experiment = current.root
    command = shlex.join(
        [sys.executable, "-m", "examples.tir.experiment", "--experiment", str(experiment)]
    )
    return f"""用户明确授权现在开始执行真实 TIR 多轮研究与训练。先保存任务，然后交接 Leader。
目标：在冻结的 train/valid/test 协议下，改进动态真实题课程、合成准入与 GRPO 训练配方，
让模型经过多轮迭代后有提升。公共执行/训练/评测服务已在 infra，研究策略在 examples/tir。

工作区：{PROJECT}。冻结实验：{experiment}。先读取 examples/tir/RESEARCH.md、protocol.json、
valid/base.json 和 valid 基线轨迹，检查冻结条件。用户要求数据合成代码和 pipeline 认真设计。
模型为 {Path(current.config.model).name}；后端使用 {current.config.runtime.repository}；
GPU 使用 {current.protocol.config.slurm.reservation} reservation，避免改动其他用户的作业。
已有阶段会持久化记录；不要重复提交。

研发权限：可修改 examples/tir/synthesis.py、compositional.py 和对应生成器测试，
可新增/修改训练 recipe、课程 plan JSON 与分析/研究报告。
examples/tir/experiment.py、benchmark.py、worker.py、workflow.py、reward.py、pipeline.py、distillation.py、
src/、AReaL 后端、protocol/freeze/harness/benchmark 数据与已有结果已冻结，禁止修改。
test 对应 official test，只供宿主独立验收。禁止读取 official test、heldout/ 数据、test/ 结果。
只能根据 valid 反馈调整策略；
真实训练池位于 protocol.benchmark.train，只能从该池读取训练题。禁止将 valid 题目
或答案直接/改写后放进训练；可用 valid 的失败模式设计独立可验证的训练题。
这是同一用户的研究约束，文件哈希会检查，但不是操作系统级隔离。

阶段命令（从工作区运行，不要同时改源码与运行阶段）：
  {command} status
  {command} round --name r1 --recipe /absolute/path/to/recipe.json
  {command} probe --name r1-real --candidate r1 --recipe /absolute/path/to/probe-recipe.json
  {command} dynamic-round --name dynamic1 --plan /absolute/path/to/curriculum-plan.json
  {command} evaluate --candidate r1  # 仅用于已训练但 valid 阶段中断的候选
recipe 按 examples.tir.experiment.RoundRecipe 解码，写明 hypothesis、parent、data 和训练参数；
algorithm="sft" 时 data=null，distillation 引用已核验的 DistillationManifest；
algorithm="grpo" 时 data 为 DataRecipe，distillation=null。
先读取已有训练池 probe 与选题证据，尽量复用有效阶段。
dynamic-round 的 plan 按 CurriculumPlan 解码，内部完成实际 parent 的试答、课程、
可选合成准入、GRPO 与 valid。参考 recipes/dynamic.json；纯真实题设 synthetic_count=0、
synthetic_proposals=0。需要严格匹配同一课程的消融时，复用其引用通过普通 round 提交。
动态课程通过 examples.tir.synthesis.dynamic_curriculum 从当前 parent 的 StudentProbe 选题，
同时保留随机真实题，不能一直沿用旧 checkpoint 的题目难度。DataRecipe.curriculum 引用其记录。
合成题由 compositional.py 的多条件任务提案，两种独立算法核验，再用当前学生重复试答；
admit_compositional 只接受同题有对有错的样本。DataRecipe.synthetic_admission 引用准入记录，
真实题与合成题至少 10:1。
轨迹蒸馏是可选分支，按用户当前选择决定；如使用，读取宿主核验后的教师 manifest。
用户已经停止 SFT 时保持停止，继续直接 GRPO，不因示例保留 SFT 能力而重新启动。
本次 ToRL 数据参考 examples/tir/recipes/r1.json。每轮新建名字，
使用上轮 checkpoint warm start 或明确分支；
优化器每轮重新初始化，不能声称恢复了优化器状态。每轮只有最后一步保存 checkpoint。
训练池是官方 ToRL 数学题，答案包括分数、根式等，评分采用 AReaL 原生 math_verify。
原整数文字题合成器只是起点，不能直接假定它适合竞赛数学。请依据训练/valid 的真实错误模式
设计合成、筛选和课程；新题需要精确数学 oracle 和独立题面验算。不得从 valid 改写出训练题。
ToRL 训练数据没有完整参考推导；可以依据实际 rollout 研发训练池难题筛选。
测试集有同题重复采样，独立置信区间由宿主按题目聚类计算；禁止将重复行当成独立题目。

完成要求：至少 2 轮真实训练和 valid 评测，建议先做 32 步，分析失败轨迹后修改 synthesis.py
或数据策略再做下一轮。不是机械续训，也不能只调整 harness 或增加评测推理预算。
记录每轮假设、代码/配方变化、valid 效果、增益与退化案例、成本，并解释下一轮选择依据。
争取 valid 相对基线至少 +5 个百分点以获得独立 test 集上的稳定收益；如未改善则继续研发。
可先做训练数据中小规模 rollout 分析筛选，禁止使用 test。运行有意义的合成验证测试。
比较全 experiment 已完成候选，包括宿主 smoke/bootstrap；这些不计作你的至少两轮真实研发。
如果最佳仍是宿主候选，应继续研发。不能只在两个退化候选中选一个并宣称改进。
选择 valid 最好的已训练候选，写 {experiment}/decision.json，格式为
{{"candidate": "候选名", "rationale": "选择依据"}}，并写 {experiment}/research-report.md。
然后通过 rsi_update_goal 完成这个 valid 研发子目标。宿主会冻结选择，再独立做最终评测；
test 绝对目标由 protocol.config.target_accuracy 指定（若非 null 则必须严格超过），
相对目标仍需达到 protocol.config.target_delta。不能提前声称已达到，也不要自行运行 test。
"""


async def run(experiment: Path, env_file: Path | None, *, resume: bool) -> None:
    if env_file is not None:
        load_dotenv(env_file, override=False)
    config = from_environment(PROJECT)
    config.auto_learn = False
    executable(config)  # Fail before creating a misleading empty research session.
    current = Experiment(experiment)
    if not (experiment / "valid/base.json").exists():
        raise ValueError("Run the frozen valid baseline before handing research to RSI")
    root = experiment / "rsi"
    receipt_path = root / "driver.json"
    system = ResearchSystem(root)
    if receipt_path.exists():
        if not resume:
            raise ValueError("A research session already exists; use --resume")
        receipt = read_record(receipt_path, DriverReceipt)
    else:
        if resume:
            raise ValueError("No existing research session to resume")
        receipt = DriverReceipt(await system.create(config), str(root), str(experiment))
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
            await system.enter_rsi(receipt.run_id, brief(current))
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
        decision = read_record(experiment / "decision.json", ResearchDecision)
        candidate = current.candidate(decision.candidate)
        if candidate.status != "completed" or candidate.valid is None:
            raise ValueError("Research decision requires a completed, evaluated candidate")
        # The host reviews valid evidence before independently freezing selection.
        print(
            f"RSI valid research completed; proposed candidate={decision.candidate}. "
            "Independent test evaluation is unrun."
        )
    finally:
        await system.close()


async def launch(args: argparse.Namespace) -> None:
    if not (PROJECT / "src/areal_rsi").is_dir():
        raise ValueError(
            "Run the TIR example from a source checkout with uv sync --locked"
        )
    root = args.experiment
    if root is None:
        if args.resume or args.action != "research":
            raise ValueError("--experiment is required for resume, status and test")
        root = (
            PROJECT
            / "runs/tir-research"
            / (datetime.now(UTC).strftime("tir-%Y%m%dT%H%M%S-") + uuid4().hex[:8])
        )
    root = root.resolve()
    if args.action == "test" and not args.candidate:
        raise ValueError("test requires --candidate NAME; review decision.json first")
    if args.action != "test" and args.candidate:
        raise ValueError("--candidate is only supported with test")
    if args.action != "research" and (args.resume or args.source):
        raise ValueError("--resume and --source are only supported with research")
    if (args.action != "research" or args.resume) and not (root / "protocol.json").exists():
        raise ValueError("No frozen experiment at --experiment; start a new research run")
    if args.env_file is not None:
        load_dotenv(args.env_file, override=False)
    if args.action == "research":
        executable(from_environment(PROJECT))
    root.mkdir(parents=True, exist_ok=True)
    print(f"Experiment: {root}", flush=True)
    if args.action == "status":
        for path in sorted((root / "valid").glob("*.json")):
            print(path)
        receipt_path = root / "rsi/driver.json"
        if receipt_path.exists():
            receipt = read_record(receipt_path, DriverReceipt)
            session = root / "rsi/sessions" / receipt.run_id / "session.json"
            if session.exists():
                print(session.read_text())
        return
    # Prevent concurrent drivers, but release the stage lock before Leader invokes stages.
    with (root / ".driver.lock").open("a") as driver:
        fcntl.flock(driver, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with (root / ".experiment.lock").open("a") as stage:
            fcntl.flock(stage, fcntl.LOCK_EX | fcntl.LOCK_NB)
            frozen = (root / "protocol.json").exists()
            if args.action == "research":
                receipt = root / "rsi/driver.json"
                if receipt.exists() and not args.resume:
                    raise ValueError("A research session already exists; use --resume")
                if frozen and args.source:
                    raise ValueError("Cannot replace benchmark in a frozen experiment")
                if not frozen:
                    config = read_record(args.config, ExperimentConfig)
                    if args.source:
                        await prepare_benchmark(config, args.source.resolve(), root)
                    prepare(config, root)
            current = Experiment(root)
            if args.action == "test":
                await current.test(args.candidate)
                print(f"Report: {root / 'report.md'}", flush=True)
                return
            print("Checking/running valid baseline…", flush=True)
            await current.evaluate("base")
        print("Starting RSI multi-round research…", flush=True)
        await run(root, args.env_file, resume=(root / "rsi/driver.json").exists())
        print(f"Review {root / 'decision.json'} before python -m examples.tir.rsi test", flush=True)


async def prepare_benchmark(config: ExperimentConfig, source: Path, root: Path) -> None:
    output = Path(config.benchmark).resolve()
    if output.name != "benchmark.json":
        raise ValueError("config.benchmark must end in benchmark.json")
    if output.parent.exists():
        raise ValueError("Benchmark output directory already exists; omit --source to reuse it")
    for name in ("train.parquet", "test.parquet"):
        if not (source / name).is_file():
            raise ValueError(f"Missing ToRL input: {source / name}")
    resources = config.slurm
    await JobExecutor(root / "execution").run(
        root.name,
        "benchmark-" + uuid4().hex[:8],
        JobSpec(
            command=(
                "srun",
                f"--reservation={resources.reservation}",
                f"--partition={resources.partition}",
                "--nodes=1",
                "--ntasks=1",
                "--cpus-per-task=2",
                "--mem=8G",
                f"--time={resources.minutes}",
                "--mpi=none",
                "apptainer",
                "exec",
                "--bind",
                "/storage:/storage",
                resources.image,
                config.tir.runtime.python,
                "-m",
                "examples.tir.benchmark",
                "--source",
                str(source),
                "--output",
                str(output.parent),
            ),
            cwd=str(PROJECT),
            env={"PYTHONPATH": f"{PROJECT / 'src'}:{PROJECT}"},
            timeout_seconds=config.tir.timeout_seconds,
        ),
    )


async def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m examples.tir.rsi", description=__doc__)
    parser.add_argument(
        "action", nargs="?", choices=("research", "status", "test"), default="research"
    )
    parser.add_argument("--experiment", type=Path, help="实验目录；新实验默认生成唯一目录")
    parser.add_argument("--config", type=Path, default=PROJECT / "examples/tir/experiment.json")
    parser.add_argument(
        "--source", type=Path, help="ToRL parquet 目录；通过 CPU Slurm 准备 benchmark"
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--candidate", help="独立 test 验收的候选名")
    args = parser.parse_args(argv)
    task = asyncio.create_task(launch(args))
    for signum in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(signum, task.cancel)
    await task


def cli(argv: list[str] | None = None) -> None:
    try:
        asyncio.run(main(argv))
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise SystemExit(130) from None


if __name__ == "__main__":
    cli()
