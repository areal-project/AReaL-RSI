"""固定 Python 编排：多条独立研究线并发迭代，轮次边界统一剪枝。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from areal_rsi.api.base import (
    BenchmarkSplit,
    ContentRef,
    EvaluationReport,
    EvolutionTarget,
    ExperimentPlan,
    ExperimentRun,
    HarnessChange,
    ResearchAgentState,
    ResearchChange,
    ResearchIteration,
    RunState,
    SolutionVersion,
    TaskResult,
    TrajectoryRef,
    VerificationRequest,
    VerificationResult,
)
from areal_rsi.api.modules import BatchHarnessRuntime
from areal_rsi.api.workflow import ResearchAgent, WorkflowContext
from areal_rsi.base.storage import FileRecords, decode, encode, now, safe_name, write_json
from areal_rsi.framework.verifier import VerifierExecutor

PRUNED = "RSI Agent 已停止此研究线"


class HarnessEvolutionWorkflow:
    name = "harness-evolution"
    _change_type: type[ResearchChange] = HarnessChange

    def __init__(
        self, root: Path, records: FileRecords, run: ExperimentRun, emit: Callable[[str], None]
    ):
        self.root, self.records, self.state, self.emit = root, records, run, emit
        self.folder = root / safe_name(run.run_id)
        path = self.folder / "progress.json"
        self.progress: dict[str, Any] = json.loads(path.read_text()) if path.exists() else {}

    def save(self) -> None:
        self.records.save(self.state.run_id, self.state, immutable=False)

    def update_progress(self, agent_id: str, **fields: Any) -> None:
        self.progress.setdefault(agent_id, {}).update(fields, updated_at=now())
        write_json(self.folder / "progress.json", self.progress)

    def _validate_context(self, context: WorkflowContext, plan: ExperimentPlan) -> None:
        if plan.target != EvolutionTarget.HARNESS or context.harness_builder is None:
            raise ValueError("当前工作流只执行 Harness 自迭代")

    async def _build_candidate(
        self, ctx: WorkflowContext, current: SolutionVersion, change: ResearchChange
    ) -> SolutionVersion:
        # 扩展工作流只替换版本构建与校验，复用下面的检查点和多研究线编排。
        assert ctx.harness_builder is not None and isinstance(change, HarnessChange)
        content = await ctx.harness_builder.build(current.harness, change)
        harness = await ctx.versions.create_harness(
            current.harness.name, content, parent_version_id=current.harness.version_id
        )
        return await ctx.versions.create_solution(
            current.model,
            harness,
            artifacts=current.artifacts,
            parent_solution_id=current.solution_id,
        )

    def _validate_candidate(
        self,
        candidate: SolutionVersion,
        current: SolutionVersion,
        baseline: SolutionVersion,
        change: ResearchChange,
    ) -> None:
        if candidate.model != baseline.model or candidate.parent_solution_id != current.solution_id:
            raise ValueError("Harness 实验禁止改变模型或丢失父版本关系")

    async def evaluate(
        self,
        ctx: WorkflowContext,
        solution: SolutionVersion,
        split: BenchmarkSplit,
        folder: Path,
        *,
        agent_id: str = "baseline",
    ) -> tuple[EvaluationReport, ContentRef]:
        tasks = await ctx.benchmark.list_tasks(split)
        if not tasks or len({t.task_id for t in tasks}) != len(tasks):
            raise ValueError("Benchmark 必须返回非空、无重复的任务集合")
        signature = {
            "benchmark": ctx.benchmark.name,
            "solution_id": solution.solution_id,
            "split": split.value,
            "tasks": encode(tasks),
        }
        identity = folder / "identity.json"
        if identity.exists() and json.loads(identity.read_text()) != signature:
            raise ValueError("评测配置/题目已改变，不能复用缓存")
        write_json(identity, signature, immutable=True)
        completed = folder / "evaluation.json"
        if completed.exists():
            cached = json.loads(completed.read_text())
            report = decode(EvaluationReport, cached["report"])
            if report.solution_id != solution.solution_id or report.split != split:
                raise ValueError("缓存评测与版本不一致")
            return report, decode(ContentRef, cached["trajectories"])
        results: list[VerificationResult] = []
        refs: list[TrajectoryRef] = []
        batch_results = None
        if isinstance(ctx.harness_runtime, BatchHarnessRuntime) and any(
            not (folder / f"task-{index}.json").exists() for index in range(1, len(tasks) + 1)
        ):
            self.update_progress(
                agent_id,
                phase=f"evaluate_{split.value}",
                task=0,
                task_count=len(tasks),
                solution_id=solution.solution_id,
            )
            batch_results = await ctx.harness_runtime.run_many(
                solution,
                tasks,
                config={
                    "execution_key": f"{self.state.run_id}/{folder.relative_to(self.folder)}",
                    "run_id": self.state.run_id,
                    "split": split.value,
                },
            )
            if set(batch_results) != {task.task_id for task in tasks}:
                raise ValueError("批量执行必须返回完整且无额外题目的结果")
        for index, task in enumerate(tasks, 1):
            self.update_progress(
                agent_id,
                phase=f"evaluate_{split.value}",
                task=index,
                task_count=len(tasks),
                solution_id=solution.solution_id,
            )
            self.emit(
                f"[{agent_id}] 评测 {split.value} {index}/{len(tasks)} · {solution.solution_id}"
            )
            checkpoint = folder / f"task-{index}.json"
            if checkpoint.exists():
                cached_task = json.loads(checkpoint.read_text())
                if cached_task["task"] != encode(task):
                    raise ValueError("任务缓存身份不匹配")
                task_result = decode(TaskResult, cached_task["result"])
                verification = decode(VerificationResult, cached_task["verification"])
                if (
                    verification.task_id != task.task_id
                    or verification.trajectory_id != task_result.trajectory.trajectory_id
                ):
                    raise ValueError("任务缓存轨迹身份不匹配")
            else:
                # 给远端实现稳定的幂等键，进程重启不重新提交同一次任务。
                task_result = (
                    batch_results[task.task_id]
                    if batch_results is not None
                    else await ctx.harness_runtime.run(
                        solution,
                        task,
                        config={
                            "execution_key": (
                                f"{self.state.run_id}/{folder.relative_to(self.folder)}/{task.task_id}"
                            ),
                        },
                    )
                )
                verification = await VerifierExecutor().verify(
                    ctx.verifier,
                    VerificationRequest(
                        task=task,
                        solution=solution,
                        result=task_result,
                        verifier=ctx.verifier_spec,
                    ),
                )
                write_json(
                    checkpoint,
                    {"task": task, "result": task_result, "verification": verification},
                    immutable=True,
                )
            results.append(verification)
            refs.append(task_result.trajectory)
        report = await ctx.evaluation.evaluate(solution, split, results)
        bundle = await ctx.trajectories.bundle(refs)
        write_json(completed, {"report": report, "trajectories": bundle}, immutable=True)
        return report, bundle

    async def step(
        self,
        ctx: WorkflowContext,
        researcher: ResearchAgent,
        agent: ResearchAgentState,
        baseline: SolutionVersion,
        baseline_report: EvaluationReport,
    ) -> None:
        iteration = len(agent.iterations) + 1
        folder = self.folder / safe_name(agent.agent_id) / f"iteration-{iteration}"
        completed = folder / "completed.json"
        if completed.exists():
            record = decode(ResearchIteration, json.loads(completed.read_text()))
            if record.iteration != iteration:
                raise ValueError("轮次检查点不匹配")
        else:
            current_id = (
                agent.iterations[-1].next_solution_id if agent.iterations else baseline.solution_id
            )
            if current_id is None:
                raise ValueError("已结束研究线不能继续提出修改")
            current = await ctx.versions.get_solution(current_id)
            if current is None:
                raise ValueError("恢复版本不存在")
            started = now()
            self.update_progress(
                agent.agent_id, iteration=iteration, phase="research", input_solution_id=current_id
            )
            train_report, bundle = await self.evaluate(
                ctx, current, BenchmarkSplit.TRAIN, folder / "train", agent_id=agent.agent_id
            )
            change_path = folder / "change.json"
            if change_path.exists():
                change = decode(self._change_type, json.loads(change_path.read_text()))
            else:
                self.update_progress(agent.agent_id, phase="propose")
                change = await researcher.propose(
                    self.state.plan.goal, self.state.plan.target, current, train_report, bundle
                )
                if not isinstance(change, self._change_type):
                    label = self._change_type.__name__.removesuffix("Change")
                    raise ValueError(f"ResearchAgent 返回了非 {label} 修改")
                write_json(change_path, change, immutable=True)
            self.update_progress(agent.agent_id, phase="build", reason=change.reason)
            self.emit(f"[{agent.agent_id}] 第 {iteration} 轮改进依据：{change.reason}")
            candidate_path = folder / "candidate.json"
            if candidate_path.exists():
                candidate = decode(SolutionVersion, json.loads(candidate_path.read_text()))
            else:
                candidate = await self._build_candidate(ctx, current, change)
                write_json(candidate_path, candidate, immutable=True)
            self._validate_candidate(candidate, current, baseline, change)
            report, _ = await self.evaluate(
                ctx,
                candidate,
                BenchmarkSplit.VALIDATE,
                folder / "validate",
                agent_id=agent.agent_id,
            )
            candidates = [baseline_report, *(i.validation_report for i in agent.iterations), report]
            self.update_progress(agent.agent_id, phase="select", score=report.score)
            selection_path = folder / "selection.json"
            if selection_path.exists():
                selected = json.loads(selection_path.read_text())["next_solution_id"]
            else:
                selected = await researcher.select(current_id, tuple(candidates))
                if selected is not None and selected not in {r.solution_id for r in candidates}:
                    raise ValueError("ResearchAgent 选择了其他研究线或未评测版本")
                write_json(selection_path, {"next_solution_id": selected}, immutable=True)
            if selected is not None and selected not in {r.solution_id for r in candidates}:
                raise ValueError("ResearchAgent 选择了其他研究线或未评测版本")
            record = ResearchIteration(
                iteration,
                current_id,
                train_report,
                bundle,
                change,
                candidate.solution_id,
                report,
                selected,
                started,
                now(),
            )
            write_json(completed, record, immutable=True)
        agent.iterations = (*agent.iterations, record)
        candidates = [
            baseline_report,
            *(i.validation_report for a in self.state.research_agents for i in a.iterations),
        ]
        # 稳定顺序决定平局，异步完成顺序不会改变最终 best。
        self.state.best_solution_id = max(candidates, key=lambda r: (r.passed, r.score)).solution_id
        if (
            record.next_solution_id is None
            or iteration >= self.state.plan.max_iterations
            or (self.state.plan.stop_when_goal_reached and record.validation_report.passed)
        ):
            agent.state = RunState.SUCCEEDED
        self.update_progress(
            agent.agent_id,
            phase="completed" if agent.state == RunState.SUCCEEDED else "iteration_completed",
            iteration=iteration,
            score=record.validation_report.score,
            next_solution_id=record.next_solution_id,
        )
        self.save()
        self.emit(
            f"[{agent.agent_id}] 第 {iteration} 轮完成：验证 {record.validation_report.score:.1%}；"
            f"下一版本 {record.next_solution_id}"
        )

    async def run(self, context: WorkflowContext, plan: ExperimentPlan) -> ExperimentRun:
        self._validate_context(context, plan)
        researchers = {a.agent_id: a for a in context.research_agents}
        if len(researchers) != plan.research_agent_count or len(researchers) != len(
            context.research_agents
        ):
            raise ValueError("ResearchAgent 数量不匹配或 ID 重复")
        baseline = await context.versions.get_solution(plan.baseline_solution_id)
        if baseline is None:
            raise ValueError("baseline 不存在")
        if not self.state.research_agents:
            self.state.research_agents = tuple(
                ResearchAgentState(key, RunState.RUNNING) for key in researchers
            )
        if set(researchers) != {a.agent_id for a in self.state.research_agents}:
            raise ValueError("恢复时 ResearchAgent 身份不能改变")
        self.state.state, self.state.error, self.state.finished_at = RunState.RUNNING, None, None
        for agent in self.state.research_agents:
            if agent.state == RunState.SUCCEEDED or agent.error == PRUNED:
                continue
            agent.state, agent.error = RunState.RUNNING, None
            self.update_progress(agent.agent_id, phase="waiting", error=None)
        self.save()
        baseline_report, _ = await self.evaluate(
            context, baseline, BenchmarkSplit.VALIDATE, self.folder / "baseline" / "validate"
        )
        self.update_progress("baseline", phase="completed", score=baseline_report.score)
        while active := [a for a in self.state.research_agents if a.state == RunState.RUNNING]:
            work = [
                asyncio.create_task(
                    self.step(context, researchers[a.agent_id], a, baseline, baseline_report)
                )
                for a in active
            ]
            try:
                outcomes = await asyncio.gather(*work, return_exceptions=True)
            except asyncio.CancelledError:
                for task in work:
                    task.cancel()
                await asyncio.gather(*work, return_exceptions=True)
                raise
            for agent, outcome in zip(active, outcomes, strict=True):
                if isinstance(outcome, BaseException):
                    agent.state = RunState.FAILED
                    agent.error = f"{type(outcome).__name__}: {outcome}"
                    self.update_progress(agent.agent_id, phase="failed", error=agent.error)
            self.save()
            remaining = {
                a.agent_id for a in self.state.research_agents if a.state == RunState.RUNNING
            }
            if len(remaining) > 1:
                # 深拷贝：控制 Agent 只能返回剪枝决定，不能直接篡改状态。
                snapshot = decode(ExperimentRun, encode(self.state))
                stopped = set(await context.rsi_agent.prune(snapshot))
                if not stopped <= remaining:
                    raise ValueError("剪枝只能选择仍在运行的研究线")
                for agent in self.state.research_agents:
                    if agent.agent_id in stopped:
                        agent.state, agent.error = RunState.CANCELLED, PRUNED
                        self.update_progress(agent.agent_id, phase="pruned", reason=PRUNED)
                self.save()
        failed = [a for a in self.state.research_agents if a.state == RunState.FAILED]
        self.state.state = RunState.FAILED if failed else RunState.SUCCEEDED
        self.state.error = "；".join(f"{a.agent_id}: {a.error}" for a in failed) or None
        self.state.finished_at = now()
        self.save()
        self.emit(
            f"RSI实验结束 run={self.state.run_id} state={self.state.state.value} "
            f"best={self.state.best_solution_id}"
        )
        return self.state
