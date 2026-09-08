"""固定 Python 编排：多条独立研究线并发迭代，轮次边界统一剪枝。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from areal_rsi.api.base import (
    AgentState,
    BenchmarkSplit,
    ContentRef,
    EvaluationReport,
    EvolutionTarget,
    ExperimentPlan,
    ExperimentRun,
    HarnessChange,
    IterationState,
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
from areal_rsi.api.workflow import ResearchAgent, WorkflowContext
from areal_rsi.base.runs import FileRunStore
from areal_rsi.base.storage import decode, encode, now, safe_name, write_json
from areal_rsi.framework.verifier import VerifierExecutor
from areal_rsi.workflows.records import EvolutionResult, evolution_history

PRUNED = "RSI Agent 已停止此研究线"


class HarnessEvolutionWorkflow:
    name = "harness-evolution"
    _change_type: type[ResearchChange] = HarnessChange

    def __init__(
        self, root: Path, records: FileRunStore, run: ExperimentRun, emit: Callable[[str], None]
    ):
        self.root, self.records, self.state, self.emit = root, records, run, emit
        self.paths = records.paths(run.run_id)
        self.folder = self.paths.workflow
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
                task_result = await ctx.harness_runtime.run(
                    solution,
                    task,
                    config={
                        "execution_key": (
                            f"{self.state.run_id}/{folder.relative_to(self.folder)}/{task.task_id}"
                        ),
                    },
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
        completed = evolution_history(self.records, agent)
        number = len(completed) + 1
        folder = self.folder / safe_name(agent.agent_id) / f"iteration-{number}"
        if not agent.iterations or agent.iterations[-1].state == IterationState.COMPLETED:
            assignment = folder / "assignment.json"
            write_json(assignment, {"instruction": self.state.plan.goal}, immutable=True)
            node = ResearchIteration(
                f"{agent.agent_id}-{number}",
                self.state.run_id,
                agent.agent_id,
                self.paths.reference(assignment),
                parent_iteration_id=agent.iterations[-1].iteration_id if agent.iterations else None,
                created_at=now(),
            )
            agent.iterations = (*agent.iterations, node)
        node = agent.iterations[-1]
        node.state, node.updated_at = IterationState.RUNNING, now()
        agent.current_iteration_id = node.iteration_id
        self.save()
        try:
            await self._step(ctx, researcher, agent, baseline, baseline_report)
        except BaseException as exc:
            node.state = (
                IterationState.CANCELLED
                if isinstance(exc, asyncio.CancelledError)
                else IterationState.FAILED
            )
            node.updated_at = now()
            node.references = tuple(
                self.paths.reference(path) for path in sorted(folder.rglob("*.json"))
            )
            self.save()
            raise

    async def _step(
        self,
        ctx: WorkflowContext,
        researcher: ResearchAgent,
        agent: ResearchAgentState,
        baseline: SolutionVersion,
        baseline_report: EvaluationReport,
    ) -> None:
        history = evolution_history(self.records, agent)
        iteration = len(history) + 1
        folder = self.folder / safe_name(agent.agent_id) / f"iteration-{iteration}"
        completed = folder / "completed.json"
        if completed.exists():
            record = decode(EvolutionResult, json.loads(completed.read_text()))
            if record.iteration != iteration:
                raise ValueError("轮次检查点不匹配")
        else:
            current_id = history[-1].next_solution_id if history else baseline.solution_id
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
            candidates = [baseline_report, *(i.validation_report for i in history), report]
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
            record = EvolutionResult(
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
        node = agent.iterations[-1]
        node.state = IterationState.COMPLETED
        node.report_ref = self.paths.reference(completed)
        node.references = tuple(
            self.paths.reference(folder / name)
            for name in (
                "change.json",
                "candidate.json",
                "train/evaluation.json",
                "validate/evaluation.json",
            )
        )
        node.updated_at = now()
        agent.current_iteration_id = None
        candidates = [
            baseline_report,
            *(
                i.validation_report
                for a in self.state.research_agents
                for i in evolution_history(self.records, a)
            ),
        ]
        # 稳定顺序决定平局，异步完成顺序不会改变最终 best。
        self.state.best_solution_id = max(candidates, key=lambda r: (r.passed, r.score)).solution_id
        if (
            record.next_solution_id is None
            or iteration >= self.state.plan.max_iterations
            or (self.state.plan.stop_when_goal_reached and record.validation_report.passed)
        ):
            agent.state = AgentState.STOPPED
        self.update_progress(
            agent.agent_id,
            phase="completed" if agent.state == AgentState.STOPPED else "iteration_completed",
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
        if plan.baseline_solution_id is None:
            raise ValueError(
                "Harness/Artifact evolution requires a baseline; the generic Run does not"
            )
        baseline = await context.versions.get_solution(plan.baseline_solution_id)
        if baseline is None:
            raise ValueError("baseline 不存在")
        if not self.state.research_agents:
            self.state.research_agents = tuple(
                ResearchAgentState(key, AgentState.RUNNING) for key in researchers
            )
        if set(researchers) != {a.agent_id for a in self.state.research_agents}:
            raise ValueError("恢复时 ResearchAgent 身份不能改变")
        self.state.state, self.state.error, self.state.finished_at = RunState.RUNNING, None, None
        for agent in self.state.research_agents:
            if agent.state == AgentState.STOPPED or agent.error == PRUNED:
                continue
            agent.state, agent.error = AgentState.RUNNING, None
            self.update_progress(agent.agent_id, phase="waiting", error=None)
        self.save()
        baseline_report, _ = await self.evaluate(
            context, baseline, BenchmarkSplit.VALIDATE, self.folder / "baseline" / "validate"
        )
        candidates = [
            baseline_report,
            *(
                i.validation_report
                for a in self.state.research_agents
                for i in evolution_history(self.records, a)
            ),
        ]
        self.state.best_solution_id = max(candidates, key=lambda r: (r.passed, r.score)).solution_id
        self.update_progress("baseline", phase="completed", score=baseline_report.score)
        while active := [a for a in self.state.research_agents if a.state == AgentState.RUNNING]:
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
                    agent.state = AgentState.FAILED
                    agent.error = f"{type(outcome).__name__}: {outcome}"
                    self.update_progress(agent.agent_id, phase="failed", error=agent.error)
            self.save()
            remaining = {
                a.agent_id for a in self.state.research_agents if a.state == AgentState.RUNNING
            }
            if len(remaining) > 1:
                # 深拷贝：控制 Agent 只能返回剪枝决定，不能直接篡改状态。
                snapshot = decode(ExperimentRun, encode(self.state))
                stopped = set(await context.rsi_agent.prune(snapshot))
                if not stopped <= remaining:
                    raise ValueError("剪枝只能选择仍在运行的研究线")
                for agent in self.state.research_agents:
                    if agent.agent_id in stopped:
                        agent.state, agent.error = AgentState.STOPPED, PRUNED
                        self.update_progress(agent.agent_id, phase="pruned", reason=PRUNED)
                self.save()
        failed = [a for a in self.state.research_agents if a.state == AgentState.FAILED]
        self.state.state = RunState.FAILED if failed else RunState.SUCCEEDED
        self.state.error = "；".join(f"{a.agent_id}: {a.error}" for a in failed) or None
        self.state.finished_at = now()
        self.save()
        self.emit(
            f"RSI实验结束 run={self.state.run_id} state={self.state.state.value} "
            f"best={self.state.best_solution_id}"
        )
        return self.state
