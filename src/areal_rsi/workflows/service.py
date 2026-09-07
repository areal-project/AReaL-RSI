"""实验服务：运行生命周期与 OS 锁独立于具体 Python Workflow。"""

from __future__ import annotations

import asyncio
import fcntl
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TypeAlias

from areal_rsi.api.base import EvolutionTarget, ExperimentPlan, ExperimentRun, RunState
from areal_rsi.api.workflow import RsiWorkflow, WorkflowContext
from areal_rsi.base.storage import FileRecords, decode, encode, new_id, now, safe_name
from areal_rsi.workflows.evolution import HarnessEvolutionWorkflow

WorkflowFactory: TypeAlias = Callable[
    [Path, FileRecords, ExperimentRun, Callable[[str], None]], RsiWorkflow
]


class LocalExperimentService:
    def __init__(
        self,
        root: Path,
        context: WorkflowContext,
        emit: Callable[[str], None],
        *,
        workflows: Mapping[str, WorkflowFactory] | None = None,
    ):
        self.root, self.context, self.emit = root, context, emit
        self.workflows = (
            dict(workflows)
            if workflows is not None
            else {"harness-evolution": HarnessEvolutionWorkflow}
        )
        self.records = FileRecords(root / "run-states")
        self.tasks: dict[str, asyncio.Task[ExperimentRun]] = {}

    def validate(self, plan: ExperimentPlan) -> None:
        if plan.workflow not in self.workflows or plan.benchmark != self.context.benchmark.name:
            raise ValueError("Workflow/Benchmark 与配置不匹配")
        if type(plan.max_iterations) is not int or plan.max_iterations < 1:
            raise ValueError("实验轮次必须为正整数")
        if plan.workflow == "harness-evolution" and plan.target != EvolutionTarget.HARNESS:
            raise ValueError("默认工作流只执行 Harness 优化")
        if (
            plan.research_agent_count != len(self.context.research_agents)
            or plan.research_agent_count < 1
        ):
            raise ValueError("计划中的 ResearchAgent 数量与装配不匹配")
        if len({a.agent_id for a in self.context.research_agents}) != plan.research_agent_count:
            raise ValueError("ResearchAgent ID 重复")

    async def _execute(self, run: ExperimentRun) -> ExperimentRun:
        locks = self.root / "locks"
        locks.mkdir(parents=True, exist_ok=True)
        # OS 锁随着进程退出释放；旧文件本身不代表任务仍在运行。
        with (locks / f"{safe_name(run.run_id)}.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("此 Run 正由另一个进程执行") from None
            # 获取锁后重读，避免等待前拿到的旧状态覆盖刚完成的另一个执行。
            latest = self.records.get(run.run_id, ExperimentRun)
            if latest is not None:
                run = latest
            if run.state == RunState.SUCCEEDED:
                return run
            try:
                workflow = self.workflows[run.plan.workflow](
                    self.root / "runs", self.records, run, self.emit
                )
                result = await workflow.run(self.context, run.plan)
                if result.run_id != run.run_id:
                    raise ValueError("Workflow 返回了其他 Run 的结果")
                self.records.save(run.run_id, result, immutable=False)
                return result
            except BaseException as exc:
                run.state = (
                    RunState.CANCELLED
                    if isinstance(exc, asyncio.CancelledError)
                    else RunState.FAILED
                )
                run.error, run.finished_at = f"{type(exc).__name__}: {exc}", now()
                for agent in run.research_agents:
                    if agent.state == RunState.RUNNING:
                        agent.state, agent.error = run.state, run.error
                self.records.save(run.run_id, run, immutable=False)
                self.emit(f"实验 {run.state.value}：{run.error}")
                return run

    async def start(self, plan: ExperimentPlan) -> ExperimentRun:
        self.validate(plan)
        if any(not task.done() for task in self.tasks.values()):
            raise ValueError("当前服务已有运行中的实验")
        plan = decode(ExperimentPlan, encode(plan))
        run = ExperimentRun(
            new_id("run"), plan, RunState.PLANNED, plan.baseline_solution_id, started_at=now()
        )
        self.records.save(run.run_id, run, immutable=False)
        self.tasks[run.run_id] = asyncio.create_task(self._execute(run))
        return run

    async def status(self, run_id: str) -> ExperimentRun | None:
        return self.records.get(run_id, ExperimentRun)

    async def list(self) -> Sequence[ExperimentRun]:
        return self.records.list(ExperimentRun)

    async def stop(self, run_id: str) -> ExperimentRun:
        task = self.tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            try:
                return await task
            except asyncio.CancelledError:
                # start 后立刻 stop，协程可能尚未进入 _execute 的异常处理。
                run = await self.status(run_id)
                assert run is not None
                run.state, run.finished_at = RunState.CANCELLED, now()
                self.records.save(run_id, run, immutable=False)
                return run
        run = await self.status(run_id)
        if run is None:
            raise KeyError(run_id)
        return run

    async def resume(self, run_id: str) -> ExperimentRun:
        run = await self.status(run_id)
        if run is None:
            raise KeyError(run_id)
        self.validate(run.plan)
        if run.state == RunState.SUCCEEDED:
            return run
        if any(not task.done() for task in self.tasks.values()):
            raise ValueError("已有运行中的实验")
        self.tasks[run_id] = asyncio.create_task(self._execute(run))
        return run
