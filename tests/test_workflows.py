"""离线工作流契约测试。模型替身只用于 UT，不作为效果或真实联调证据。"""

from __future__ import annotations

import asyncio
import difflib
import fcntl
import json
import tempfile
import unittest
from collections.abc import Callable, Sequence
from pathlib import Path
from unittest.mock import AsyncMock, patch

from areal_rsi.api.base import (
    BenchmarkSplit,
    ContentRef,
    EvaluationReport,
    EvolutionTarget,
    ExperimentPlan,
    ExperimentRun,
    HarnessChange,
    JsonObject,
    RunState,
    SolutionVersion,
    Task,
    TaskResult,
    Trajectory,
    VerificationRequest,
    VerificationResult,
    VerifierSpec,
)
from areal_rsi.api.workflow import WorkflowContext
from areal_rsi.base.evidence import FileEvidenceStore
from areal_rsi.base.storage import (
    FileContentStore,
    FileMemory,
    FileTrajectoryStore,
    FileVersionManager,
    decode,
    new_id,
    now,
)
from areal_rsi.modules.benchmark import AccuracyEvaluation, ArithmeticBenchmark, ArithmeticVerifier
from areal_rsi.modules.harness import LocalHarnessBuilder
from areal_rsi.workflows.evolution import PRUNED
from areal_rsi.workflows.service import LocalExperimentService


class TaskFixture:
    def __init__(self, content: FileContentStore, trajectories: FileTrajectoryStore):
        self.content, self.trajectories = content, trajectories
        self.calls = self.active = self.peak = 0

    async def run(
        self, solution: SolutionVersion, task: Task, *, config: JsonObject | None = None
    ) -> TaskResult:
        assert config and config["execution_key"]
        self.calls += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0)
            # 所有版本均错误，证明工作流不会按 iteration 制造分数增长。
            output = await self.content.put_json(
                {
                    "task_id": task.task_id,
                    "solution_id": solution.solution_id,
                    "response": "#### -999",
                }
            )
            trace = await self.trajectories.save(
                Trajectory(
                    new_id("trace"),
                    "task-fixture",
                    await self.content.put_json({"turns": []}),
                    {},
                    now(),
                    task_id=task.task_id,
                    solution_id=solution.solution_id,
                )
            )
            return TaskResult(output, trace)
        finally:
            self.active -= 1


class ResearchFixture:
    def __init__(self, agent_id: str, content: FileContentStore):
        self.agent_id, self.content = agent_id, content
        self.proposals = self.selections = 0
        self.goals: list[str] = []
        self.fail_selection = False
        self.started = asyncio.Event()
        self.block: asyncio.Event | None = None

    async def propose(
        self,
        goal: str,
        target: EvolutionTarget,
        current: SolutionVersion,
        report: EvaluationReport,
        trajectories: ContentRef,
    ) -> HarnessChange:
        assert target == EvolutionTarget.HARNESS and report.split == BenchmarkSplit.TRAIN
        assert report.solution_id == current.solution_id
        bundle = await FileEvidenceStore(self.content).get(trajectories)
        assert bundle.metadata == {"solution_id": current.solution_id, "split": "train"}
        files = {entry.path: entry for entry in bundle.files}
        assert (
            json.loads(await self.content.read_text(files["report.json"].content))["split"]
            == "train"
        )
        for entry in bundle.files:
            if entry.kind != "verification":
                continue
            verification = await self.content.read_json(entry.content)
            assert isinstance(verification, dict)
            assert verification["task_id"] == entry.metadata["task_id"]
            assert verification["trajectory_id"] == entry.metadata["trajectory_id"]
            prefix = entry.path.rsplit("/", 1)[0]
            output = await self.content.read_json(files[f"{prefix}/output"].content)
            assert isinstance(output, dict)
            assert output["task_id"] == verification["task_id"]
            assert await self.content.read_json(files[f"{prefix}/trajectory.json"].content) == {
                "turns": []
            }
        self.goals.append(goal)
        self.proposals += 1
        self.started.set()
        if self.block is not None:
            await self.block.wait()
        manifest = json.loads(await self.content.read_text(current.harness.content))
        old = await self.content.read_text(decode(ContentRef, manifest["files"]["prompt.md"]))
        new = old + f"\nIndependent research line: {self.agent_id}; proposal {self.proposals}.\n"
        patch = "".join(
            difflib.unified_diff(
                old.splitlines(True),
                new.splitlines(True),
                fromfile="a/prompt.md",
                tofile="b/prompt.md",
            )
        )
        return HarnessChange(await self.content.put_text(patch), f"{self.agent_id} rationale")

    async def select(
        self, current_solution_id: str, candidates: Sequence[EvaluationReport]
    ) -> str | None:
        assert all(r.split == BenchmarkSplit.VALIDATE for r in candidates)
        assert current_solution_id in {r.solution_id for r in candidates}
        self.selections += 1
        if self.fail_selection:
            self.fail_selection = False
            raise RuntimeError("fixture selection interruption")
        return candidates[-1].solution_id


class RsiFixture:
    def __init__(self) -> None:
        self.stop_ids: tuple[str, ...] = ()
        self.calls = 0

    async def chat(self, message: str) -> str | ExperimentPlan:
        return "fixture"

    async def prune(self, run: ExperimentRun) -> Sequence[str]:
        self.calls += 1
        # 修改传入对象不应篡改真实状态。
        run.best_solution_id = "malicious-fixture"
        return self.stop_ids


class WorkflowTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.content = FileContentStore(self.root / "content")
        self.versions = FileVersionManager(self.root / "versions")
        self.trajectories = FileTrajectoryStore(self.root / "trajectories", self.content)
        harness_dir = self.root / "initial"
        harness_dir.mkdir()
        (harness_dir / "prompt.md").write_text("Return a numeric answer.\n")
        (harness_dir / "mcp.json").write_text('{"servers": []}')
        self.baseline = await self.versions.create_solution(
            await self.versions.create_model(
                "task-agent-fixture-model", await self.content.put_text("fixed")
            ),
            await self.versions.create_harness("fixture", await self.content.snapshot(harness_dir)),
        )
        self.runtime = TaskFixture(self.content, self.trajectories)
        self.researchers = tuple(
            ResearchFixture(f"research-agent-{i}", self.content) for i in (1, 2)
        )
        self.rsi = RsiFixture()
        self.context = WorkflowContext(
            self.content,
            FileMemory(self.root / "memory"),
            self.trajectories,
            self.versions,
            self.runtime,
            self.rsi,
            self.researchers,
            ArithmeticBenchmark(),
            ArithmeticVerifier(self.content),
            VerifierSpec("arithmetic-verifier", "injected"),
            AccuracyEvaluation(),
            LocalHarnessBuilder(self.content, self.root / "builds"),
        )
        self.service = LocalExperimentService(self.root, self.context, lambda _: None)
        self.plan = ExperimentPlan(
            "fixture research",
            "harness-evolution",
            "arithmetic-v1",
            self.baseline.solution_id,
            EvolutionTarget.HARNESS,
            3,
            research_agent_count=2,
            stop_when_goal_reached=False,
        )

    async def execute(self) -> ExperimentRun:
        run = await self.service.start(self.plan)
        return await self.service.tasks[run.run_id]

    async def test_bounded_tasks_and_verifier_resume_reuse_generated_outputs(self) -> None:
        self.plan.research_agent_count = 1
        self.plan.max_iterations = 1
        self.context.research_agents = self.researchers[:1]
        self.context.evaluation_concurrency = 2
        original = self.context.verifier.verify
        calls = 0

        async def flaky(request: VerificationRequest) -> VerificationResult:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("temporary verifier outage")
            return await original(request)

        with patch.object(self.context.verifier, "verify", AsyncMock(side_effect=flaky)):
            run = await self.execute()
        self.assertEqual(run.state, RunState.FAILED)
        self.assertEqual(self.runtime.calls, 3)
        self.assertEqual(calls, 3)
        self.assertEqual(self.runtime.peak, 2)
        folder = self.root / "runs" / run.run_id / "baseline/validate"
        self.assertEqual(len(list(folder.glob("generation-*.json"))), 3)
        self.assertEqual(len(list(folder.glob("task-*.json"))), 2)
        resumed = await self.service.resume(run.run_id)
        finished = await self.service.tasks[resumed.run_id]
        self.assertEqual(finished.state, RunState.SUCCEEDED, finished.error)
        # Three initial generations plus TRAIN and candidate VALIDATE, no regenerated baseline.
        self.assertEqual(self.runtime.calls, 9)
        self.assertEqual(len(list(folder.glob("task-*.json"))), 3)

    async def test_parallel_independent_lines_and_honest_flat_scores(self) -> None:
        run = await self.execute()
        self.assertEqual(run.state.value, "succeeded", run.error)
        self.assertGreaterEqual(self.runtime.peak, 2)
        self.assertEqual([len(a.iterations) for a in run.research_agents], [3, 3])
        self.assertTrue(
            all(research.goals == [self.plan.goal] * 3 for research in self.researchers)
        )
        self.assertEqual(run.best_solution_id, self.baseline.solution_id)
        self.assertEqual(self.runtime.calls, 3 + 2 * 3 * 6)
        for agent in run.research_agents:
            parent = self.baseline.solution_id
            for iteration in agent.iterations:
                self.assertEqual(iteration.input_solution_id, parent)
                self.assertEqual(iteration.validation_report.score, 0)
                child = await self.versions.get_solution(iteration.candidate_solution_id)
                assert child is not None
                self.assertEqual(child.model, self.baseline.model)
                self.assertEqual(child.parent_solution_id, parent)
                assert iteration.next_solution_id is not None
                parent = iteration.next_solution_id
        self.assertEqual(self.rsi.calls, 2)

    async def test_prune_is_persistent_and_does_not_reset_other_line(self) -> None:
        self.rsi.stop_ids = ("research-agent-2",)
        run = await self.execute()
        self.assertEqual(run.state.value, "succeeded", run.error)
        first, second = run.research_agents
        self.assertEqual((len(first.iterations), len(second.iterations)), (3, 1))
        self.assertEqual(second.error, PRUNED)
        self.assertEqual(second.state.value, "cancelled")
        self.assertEqual((await self.service.resume(run.run_id)).state.value, "succeeded")
        self.assertEqual(self.researchers[1].proposals, 1)

    async def test_failed_line_resumes_without_repeating_finished_evaluations(self) -> None:
        self.researchers[1].fail_selection = True
        run = await self.execute()
        self.assertEqual(run.state.value, "failed")
        self.assertEqual([len(a.iterations) for a in run.research_agents], [3, 0])
        calls = self.runtime.calls
        await self.service.resume(run.run_id)
        finished = await self.service.tasks[run.run_id]
        self.assertEqual(finished.state.value, "succeeded", finished.error)
        self.assertEqual(self.runtime.calls, calls + 2 * 6)
        self.assertEqual([r.proposals for r in self.researchers], [3, 3])
        self.assertEqual([len(a.iterations) for a in finished.research_agents], [3, 3])

    async def test_cancel_and_resume_keeps_completed_train_tasks(self) -> None:
        self.researchers[1].block = asyncio.Event()
        run = await self.service.start(self.plan)
        await asyncio.wait_for(self.researchers[1].started.wait(), timeout=5)
        stopped = await self.service.stop(run.run_id)
        self.assertEqual(stopped.state.value, "cancelled")
        # 完成的 TRAIN task 文件应被复用，不再为这个 checkpoint 调用 runtime。
        checkpoints = list((self.root / "runs" / run.run_id).rglob("task-*.json"))
        original = {path: path.read_bytes() for path in checkpoints}
        self.researchers[1].block.set()
        await self.service.resume(run.run_id)
        finished = await self.service.tasks[run.run_id]
        self.assertEqual(finished.state.value, "succeeded", finished.error)
        for path, data in original.items():
            self.assertEqual(path.read_bytes(), data)

    async def test_stop_before_execution_and_cross_process_lock(self) -> None:
        run = await self.service.start(self.plan)
        self.assertEqual((await self.service.stop(run.run_id)).state.value, "cancelled")
        locks = self.root / "locks"
        locks.mkdir(exist_ok=True)
        with (locks / f"{run.run_id}.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            await self.service.resume(run.run_id)
            with self.assertRaisesRegex(RuntimeError, "另一个进程"):
                await self.service.tasks[run.run_id]

    async def test_resume_rejects_changed_benchmark(self) -> None:
        run = await self.service.start(self.plan)
        await self.service.stop(run.run_id)
        self.context.benchmark = ChangedBenchmark()
        with self.assertRaisesRegex(ValueError, "Benchmark"):
            await self.service.resume(run.run_id)

    async def test_workflow_initialization_failure_is_persisted(self) -> None:
        from areal_rsi.api.workflow import RsiWorkflow
        from areal_rsi.base.storage import FileRecords

        def factory(
            root: Path, records: FileRecords, run: ExperimentRun, emit: Callable[[str], None]
        ) -> RsiWorkflow:
            raise RuntimeError("workflow initialization failed")

        service = LocalExperimentService(
            self.root, self.context, lambda _: None, workflows={"broken-python": factory}
        )
        self.plan.workflow = "broken-python"
        run = await service.start(self.plan)
        failed = await service.tasks[run.run_id]
        self.assertEqual(failed.state, RunState.FAILED)
        self.assertIn("initialization failed", failed.error or "")
        self.assertEqual(await service.status(run.run_id), failed)

    async def test_service_accepts_a_registered_python_workflow(self) -> None:
        from areal_rsi.api.workflow import RsiWorkflow
        from areal_rsi.base.storage import FileRecords

        calls: list[str] = []

        def factory(
            root: Path, records: FileRecords, run: ExperimentRun, emit: Callable[[str], None]
        ) -> RsiWorkflow:
            class CustomWorkflow:
                name = "custom-python"

                async def run(
                    self, context: WorkflowContext, plan: ExperimentPlan
                ) -> ExperimentRun:
                    calls.append(plan.goal)
                    run.state = RunState.SUCCEEDED
                    return run

            return CustomWorkflow()

        service = LocalExperimentService(
            self.root, self.context, lambda _: None, workflows={"custom-python": factory}
        )
        self.plan.workflow = "custom-python"
        run = await service.start(self.plan)
        finished = await service.tasks[run.run_id]
        self.assertEqual(finished.state, RunState.SUCCEEDED)
        self.assertEqual(calls, [self.plan.goal])
        self.assertEqual((await service.status(run.run_id)), finished)


class ChangedBenchmark(ArithmeticBenchmark):
    name = "changed-benchmark"
