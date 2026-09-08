"""Artifact 编排的离线契约测试；替身分数不代表真实产物效果。"""

from __future__ import annotations

import asyncio
import difflib
import json
import tempfile
import unittest
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, Mock, patch

from areal_rsi.agents.research.artifact import ArtifactResearchAgent
from areal_rsi.api.base import (
    AgentSpec,
    ArtifactChange,
    BenchmarkSplit,
    ContentRef,
    EvaluationReport,
    EvolutionTarget,
    ExperimentPlan,
    ExperimentRun,
    HarnessChange,
    JsonObject,
    ResearchChange,
    RunState,
    SolutionVersion,
    Task,
    TaskResult,
    Trajectory,
    TrajectoryRef,
    VerificationRequest,
    VerificationResult,
    VerifierSpec,
)
from areal_rsi.api.workflow import WorkflowContext
from areal_rsi.base.storage import (
    FileContentStore,
    FileMemory,
    FileTrajectoryStore,
    FileVersionManager,
    decode,
    new_id,
    now,
    write_json,
)
from areal_rsi.modules.artifact import LocalArtifactBuilder
from areal_rsi.modules.runtime import DshAgentRuntime, DshSession
from areal_rsi.workflows.artifact import ArtifactEvolutionWorkflow
from areal_rsi.workflows.evolution import PRUNED, HarnessEvolutionWorkflow
from areal_rsi.workflows.service import LocalExperimentService


class ArtifactBenchmarkFixture:
    name = "artifact-fixture-v1"

    async def list_tasks(self, split: BenchmarkSplit) -> Sequence[Task]:
        return [Task(f"{split.value}_{i}", {"check": "fixture"}) for i in (1, 2)]


class ArtifactRuntimeFixture:
    def __init__(self, content: FileContentStore, trajectories: FileTrajectoryStore):
        self.content, self.trajectories = content, trajectories
        self.calls = self.active = self.peak = 0
        self.fail_key_suffix: str | None = None

    async def run(
        self, solution: SolutionVersion, task: Task, *, config: JsonObject | None = None
    ) -> TaskResult:
        assert config is not None and isinstance(config["execution_key"], str)
        self.calls += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0)
            if self.fail_key_suffix and config["execution_key"].endswith(self.fail_key_suffix):
                self.fail_key_suffix = None
                raise RuntimeError("fixture task interruption")
            output = await self.content.put_json(
                {
                    "task_id": task.task_id,
                    "solution_id": solution.solution_id,
                    "artifacts": [a.version_id for a in solution.artifacts],
                }
            )
            trace = await self.trajectories.save(
                Trajectory(
                    new_id("trace"),
                    "artifact-runtime-fixture",
                    output,
                    {},
                    now(),
                    task_id=task.task_id,
                    solution_id=solution.solution_id,
                )
            )
            return TaskResult(output, trace)
        finally:
            self.active -= 1


class ArtifactVerifierFixture:
    def __init__(self, content: FileContentStore):
        self.content = content

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        task, solution, result = request.task, request.solution, request.result
        output = json.loads(await self.content.read_text(result.output))
        assert output == {
            "task_id": task.task_id,
            "solution_id": solution.solution_id,
            "artifacts": [a.version_id for a in solution.artifacts],
        }
        # 固定非零但未达标的分数，检查工作流原样使用注入的聚合结果。
        return VerificationResult(
            task.task_id, result.trajectory.trajectory_id, False, 0.25, {}, "fixture only"
        )


class ArtifactEvaluationFixture:
    async def evaluate(
        self,
        solution: SolutionVersion,
        split: BenchmarkSplit,
        results: Sequence[VerificationResult],
        *,
        baseline: EvaluationReport | None = None,
    ) -> EvaluationReport:
        passed = [result.passed for result in results]
        scores = [result.score for result in results]
        assert all(value is not None for value in passed)
        assert all(value is not None for value in scores)
        return EvaluationReport(
            solution.solution_id,
            split,
            all(value for value in passed if value is not None),
            sum(value for value in scores if value is not None) / len(results),
            {"fixture_tasks": float(len(results))},
            now(),
        )


class ArtifactResearchFixture:
    def __init__(self, agent_id: str, content: FileContentStore):
        self.agent_id, self.content = agent_id, content
        self.artifact_name = "product"
        self.proposals = 0
        self.fail_selection = self.wrong_change = False

    async def propose(
        self,
        goal: str,
        target: EvolutionTarget,
        current: SolutionVersion,
        report: EvaluationReport,
        trajectories: ContentRef,
    ) -> ResearchChange:
        assert target == EvolutionTarget.ARTIFACT and report.split == BenchmarkSplit.TRAIN
        assert report.solution_id == current.solution_id
        self.proposals += 1
        base = next((a for a in current.artifacts if a.name == self.artifact_name), None)
        old = ""
        if base:
            manifest = json.loads(await self.content.read_text(base.content))
            old = await self.content.read_text(decode(ContentRef, manifest["files"]["main.txt"]))
        new = old + f"{self.agent_id}: proposal {self.proposals}\n"
        patch = await self.content.put_text(
            "".join(
                difflib.unified_diff(
                    old.splitlines(True),
                    new.splitlines(True),
                    fromfile="a/main.txt" if base else "/dev/null",
                    tofile="b/main.txt",
                )
            )
        )
        if self.wrong_change:
            return HarnessChange(patch, "wrong target fixture")
        return ArtifactChange(self.artifact_name, patch, "fixture product change")

    async def select(
        self, current_solution_id: str, candidates: Sequence[EvaluationReport]
    ) -> str | None:
        assert all(report.split == BenchmarkSplit.VALIDATE for report in candidates)
        if self.fail_selection:
            self.fail_selection = False
            raise RuntimeError("fixture selection interruption")
        return candidates[-1].solution_id


class ArtifactRsiFixture:
    def __init__(self) -> None:
        self.stop_ids: tuple[str, ...] = ()

    async def chat(self, message: str) -> str | ExperimentPlan:
        return "fixture"

    async def prune(self, run: ExperimentRun) -> Sequence[str]:
        return self.stop_ids


class ArtifactWorkflowTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.content = FileContentStore(self.root / "content")
        self.versions = FileVersionManager(self.root / "versions")
        self.trajectories = FileTrajectoryStore(self.root / "trajectories", self.content)
        initial = self.root / "initial"
        initial.mkdir()
        (initial / "main.txt").write_text("initial product\n")
        self.product = await self.versions.create_artifact(
            "product", await self.content.snapshot(initial)
        )
        self.other = await self.versions.create_artifact(
            "reference", await self.content.put_text("unchanged")
        )
        self.baseline = await self.versions.create_solution(
            await self.versions.create_model(
                "task-agent-fixture-model", await self.content.put_text("model")
            ),
            await self.versions.create_harness(
                "fixed-harness", await self.content.put_text("code")
            ),
            artifacts=(self.product, self.other),
        )
        self.runtime = ArtifactRuntimeFixture(self.content, self.trajectories)
        self.researchers = tuple(
            ArtifactResearchFixture(f"research-agent-{i}", self.content) for i in (1, 2)
        )
        self.rsi = ArtifactRsiFixture()
        self.context = WorkflowContext(
            self.content,
            FileMemory(self.root / "memory"),
            self.trajectories,
            self.versions,
            self.runtime,
            self.rsi,
            self.researchers,
            ArtifactBenchmarkFixture(),
            ArtifactVerifierFixture(self.content),
            VerifierSpec("artifact-verifier", "injected"),
            ArtifactEvaluationFixture(),
            artifact_builder=LocalArtifactBuilder(self.content, self.root / "builds"),
        )
        self.service = self.make_service()
        self.plan = ExperimentPlan(
            "fixture artifact research",
            "artifact-evolution",
            self.context.benchmark.name,
            self.baseline.solution_id,
            EvolutionTarget.ARTIFACT,
            3,
            research_agent_count=2,
            stop_when_goal_reached=False,
        )

    def make_service(self) -> LocalExperimentService:
        return LocalExperimentService(
            self.root,
            self.context,
            lambda _: None,
            workflows={
                "harness-evolution": HarnessEvolutionWorkflow,
                "artifact-evolution": ArtifactEvolutionWorkflow,
            },
        )

    def single_line(self, iterations: int = 1) -> None:
        self.context.research_agents = (self.researchers[0],)
        self.plan.research_agent_count, self.plan.max_iterations = 1, iterations

    async def execute(self) -> ExperimentRun:
        run = await self.service.start(self.plan)
        return await self.service.tasks[run.run_id]

    async def test_parallel_lines_preserve_components_and_artifact_parent_chains(self) -> None:
        run = await self.execute()
        self.assertEqual(run.state, RunState.SUCCEEDED, run.error)
        self.assertEqual([len(a.iterations) for a in run.research_agents], [3, 3])
        self.assertGreaterEqual(self.runtime.peak, 2)
        self.assertEqual(self.runtime.calls, 2 + 2 * 3 * 4)
        self.assertEqual(run.best_solution_id, self.baseline.solution_id)
        for agent in run.research_agents:
            parent = self.baseline
            for iteration in agent.iterations:
                self.assertIsInstance(iteration.change, ArtifactChange)
                self.assertEqual(iteration.input_solution_id, parent.solution_id)
                self.assertEqual(iteration.validation_report.score, 0.25)
                child = await self.versions.get_solution(iteration.candidate_solution_id)
                assert child is not None
                self.assertEqual(child.model, self.baseline.model)
                self.assertEqual(child.harness, self.baseline.harness)
                self.assertEqual(child.parent_solution_id, parent.solution_id)
                self.assertEqual(child.artifacts[1], self.other)
                self.assertEqual(
                    child.artifacts[0].parent_version_id, parent.artifacts[0].version_id
                )
                self.assertNotEqual(child.artifacts[0].content, parent.artifacts[0].content)
                parent = child
        self.assertEqual(await self.versions.get_artifact(self.product.version_id), self.product)
        saved = await self.service.status(run.run_id)
        self.assertEqual(saved, run)
        assert saved is not None
        self.assertIsInstance(saved.research_agents[0].iterations[0].change, ArtifactChange)

    async def test_creates_first_artifact_and_then_updates_it(self) -> None:
        self.single_line(iterations=2)
        empty = await self.versions.create_solution(self.baseline.model, self.baseline.harness)
        self.plan.baseline_solution_id = empty.solution_id
        run = await self.execute()
        self.assertEqual(run.state, RunState.SUCCEEDED, run.error)
        parent_artifact_id = None
        for iteration in run.research_agents[0].iterations:
            candidate = await self.versions.get_solution(iteration.candidate_solution_id)
            assert candidate is not None
            self.assertEqual(len(candidate.artifacts), 1)
            self.assertEqual(candidate.artifacts[0].parent_version_id, parent_artifact_id)
            parent_artifact_id = candidate.artifacts[0].version_id
        self.assertEqual(empty.artifacts, ())

    async def test_real_artifact_agent_creates_iterates_and_resumes_selection(self) -> None:
        self.single_line(iterations=2)
        empty = await self.versions.create_solution(self.baseline.model, self.baseline.harness)
        self.plan.baseline_solution_id = empty.solution_id
        memory = FileMemory(self.root / "integration-memory")
        self.context.memory = memory
        model_runtime = DshAgentRuntime(
            self.root / "model-sessions",
            self.content,
            self.trajectories,
            base_url="https://offline-fixture.invalid",
            api_key="offline-fixture-not-a-credential",
        )
        # 只替换模型 Session；提案解析、diff、选版、构建和检查点均运行真实实现。
        responses: list[str | Exception] = [
            '{"files":{"main.txt":"first artifact\\n"},"reason":"fixture creation"}',
            RuntimeError("fixture model selection interruption"),
            '{"candidate_index":1}',
            '{"files":{"main.txt":"first artifact\\nsecond change\\n"},'
            '"reason":"fixture iteration"}',
            '{"candidate_index":2}',
        ]
        sessions: list[Mock] = []

        async def open_session(
            specification: AgentSpec, *, workspace_root: str | None = None
        ) -> DshSession:
            assert workspace_root is not None
            workspace = Path(workspace_root)
            index = len(sessions)
            response = responses[index]
            session = Mock(spec=DshSession)
            sessions.append(session)

            async def send(prompt: str) -> str:
                if isinstance(response, Exception):
                    raise response
                data = json.loads(prompt)
                if index in (0, 3):
                    self.assertEqual(data["artifact_name"], "product")
                    self.assertEqual(data["creating"], index == 0)
                    self.assertNotIn("report", data)
                    report = json.loads((workspace / "train/report.json").read_text())
                    self.assertEqual(report["split"], "train")
                    traces = list((workspace / "train").glob("tasks/*/trajectory.json"))
                    self.assertEqual(len(traces), 2)
                    for path in traces:
                        trace = json.loads(path.read_text())
                        self.assertTrue(trace["task_id"].startswith("train_"))
                        self.assertEqual(trace["solution_id"], report["solution_id"])
                    if index == 0:
                        self.assertEqual(list((workspace / "artifact").iterdir()), [])
                    else:
                        self.assertEqual(
                            (workspace / "artifact" / "main.txt").read_text(), "first artifact\n"
                        )
                        self.assertEqual(
                            json.loads((workspace / "memory.json").read_text()),
                            [{"text": "fixture creation"}],
                        )
                else:
                    self.assertTrue(
                        all(item["report"]["split"] == "validate" for item in data["candidates"])
                    )
                return response

            async def finish() -> TrajectoryRef:
                return await self.trajectories.save(
                    Trajectory(
                        f"artifact-agent-session-{index}",
                        specification.agent_id,
                        await self.content.put_json({"fixture": True, "turns": []}),
                        {},
                        now(),
                    )
                )

            session.send = AsyncMock(side_effect=send)
            session.finish = AsyncMock(side_effect=finish)
            return cast(DshSession, session)

        def researcher() -> ArtifactResearchAgent:
            return ArtifactResearchAgent(
                "artifact-research",
                model_runtime,
                empty.model,
                self.content,
                memory,
                self.versions,
                self.root / "integration-workspaces",
                artifact_name="product",
            )

        with patch.object(model_runtime, "open", side_effect=open_session):
            self.context.research_agents = (researcher(),)
            run = await self.execute()
            self.assertEqual(run.state, RunState.FAILED)
            self.assertIn("fixture model selection interruption", run.error or "")
            self.assertEqual(len(run.research_agents[0].iterations), 0)
            self.assertEqual(self.runtime.calls, 6)
            checkpoint = (
                self.root
                / "runs"
                / run.run_id
                / "artifact-research"
                / "iteration-1"
                / "candidate.json"
            )
            original = checkpoint.read_bytes()
            self.context.research_agents = (researcher(),)
            self.service = self.make_service()
            await self.service.resume(run.run_id)
            finished = await self.service.tasks[run.run_id]

        self.assertEqual(finished.state, RunState.SUCCEEDED, finished.error)
        self.assertEqual(self.runtime.calls, 10)
        self.assertEqual(checkpoint.read_bytes(), original)
        self.assertEqual(len(sessions), 5)
        for session in sessions:
            session.send.assert_awaited_once()
            session.finish.assert_awaited_once()
        iterations = finished.research_agents[0].iterations
        self.assertEqual(len(iterations), 2)
        parent = empty
        for index, iteration in enumerate(iterations):
            candidate = await self.versions.get_solution(iteration.candidate_solution_id)
            assert candidate is not None
            self.assertIsInstance(iteration.change, ArtifactChange)
            self.assertEqual(iteration.next_solution_id, candidate.solution_id)
            self.assertEqual(iteration.input_solution_id, parent.solution_id)
            self.assertEqual(candidate.parent_solution_id, parent.solution_id)
            self.assertEqual((candidate.model, candidate.harness), (empty.model, empty.harness))
            self.assertEqual(len(candidate.artifacts), 1)
            artifact = candidate.artifacts[0]
            self.assertEqual(artifact.name, "product")
            self.assertEqual(
                artifact.parent_version_id, parent.artifacts[0].version_id if index else None
            )
            output = await self.content.materialize(artifact.content, self.root / f"result-{index}")
            expected = "first artifact\n" + ("second change\n" if index else "")
            self.assertEqual((output / "main.txt").read_text(), expected)
            parent = candidate
        memories = await memory.search("fixture")
        self.assertEqual(
            {hit.memory.text for hit in memories}, {"fixture creation", "fixture iteration"}
        )

    async def test_adds_an_artifact_without_replacing_existing_ones(self) -> None:
        self.single_line()
        self.researchers[0].artifact_name = "extra"
        run = await self.execute()
        self.assertEqual(run.state, RunState.SUCCEEDED, run.error)
        candidate = await self.versions.get_solution(
            run.research_agents[0].iterations[0].candidate_solution_id
        )
        assert candidate is not None
        self.assertEqual(candidate.artifacts[:2], self.baseline.artifacts)
        self.assertEqual(candidate.artifacts[2].name, "extra")
        self.assertIsNone(candidate.artifacts[2].parent_version_id)

    async def test_resume_reuses_built_candidate_and_evaluations(self) -> None:
        self.researchers[1].fail_selection = True
        run = await self.execute()
        self.assertEqual(run.state, RunState.FAILED)
        self.assertEqual([len(a.iterations) for a in run.research_agents], [3, 0])
        calls = self.runtime.calls
        folder = self.root / "runs" / run.run_id / "research-agent-2" / "iteration-1"
        original = {path: path.read_bytes() for path in folder.rglob("*.json")}
        self.service = self.make_service()
        await self.service.resume(run.run_id)
        finished = await self.service.tasks[run.run_id]
        self.assertEqual(finished.state, RunState.SUCCEEDED, finished.error)
        self.assertEqual(self.runtime.calls, calls + 2 * 4)
        self.assertEqual([a.proposals for a in self.researchers], [3, 3])
        self.assertEqual([len(a.iterations) for a in finished.research_agents], [3, 3])
        for path, data in original.items():
            self.assertEqual(path.read_bytes(), data)

    async def test_resume_reuses_completed_task_before_runtime_failure(self) -> None:
        self.single_line()
        self.runtime.fail_key_suffix = "iteration-1/train/train_2"
        run = await self.execute()
        self.assertEqual(run.state, RunState.FAILED)
        checkpoint = (
            self.root
            / "runs"
            / run.run_id
            / "research-agent-1"
            / "iteration-1"
            / "train"
            / "task-1.json"
        )
        original, calls = checkpoint.read_bytes(), self.runtime.calls
        self.service = self.make_service()
        await self.service.resume(run.run_id)
        finished = await self.service.tasks[run.run_id]
        self.assertEqual(finished.state, RunState.SUCCEEDED, finished.error)
        self.assertEqual(self.runtime.calls, calls + 3)
        self.assertEqual(checkpoint.read_bytes(), original)
        self.assertEqual(self.researchers[0].proposals, 1)

    async def test_prunes_artifact_line_at_iteration_boundary(self) -> None:
        self.rsi.stop_ids = ("research-agent-2",)
        run = await self.execute()
        self.assertEqual(run.state, RunState.SUCCEEDED, run.error)
        self.assertEqual([len(a.iterations) for a in run.research_agents], [3, 1])
        self.assertEqual(run.research_agents[1].error, PRUNED)
        self.assertEqual(run.research_agents[1].state, RunState.CANCELLED)

    async def test_default_service_does_not_enable_artifact_workflow(self) -> None:
        service = LocalExperimentService(self.root, self.context, lambda _: None)
        self.assertEqual(set(service.workflows), {"harness-evolution"})
        with self.assertRaisesRegex(ValueError, "Workflow"):
            await service.start(self.plan)

    async def test_rejects_wrong_target_and_missing_builder(self) -> None:
        self.plan.target = EvolutionTarget.HARNESS
        run = await self.execute()
        self.assertEqual(run.state, RunState.FAILED)
        self.assertIn("ARTIFACT", run.error or "")
        self.plan.target = EvolutionTarget.ARTIFACT
        self.context.artifact_builder = None
        run = await self.execute()
        self.assertEqual(run.state, RunState.FAILED)
        self.assertIn("artifact_builder", run.error or "")
        self.assertEqual(self.runtime.calls, 0)

    async def test_rejects_harness_change_from_artifact_researcher(self) -> None:
        self.single_line()
        self.researchers[0].wrong_change = True
        run = await self.execute()
        self.assertEqual(run.state, RunState.FAILED)
        self.assertIn("非 Artifact 修改", run.error or "")
        self.assertEqual(await self.versions.list_child_solutions(self.baseline.solution_id), [])

    async def test_resume_checks_cached_candidate_components_and_parent_links(self) -> None:
        self.single_line()
        self.researchers[0].fail_selection = True
        run = await self.execute()
        self.assertEqual(run.state, RunState.FAILED)
        path = (
            self.root / "runs" / run.run_id / "research-agent-1" / "iteration-1" / "candidate.json"
        )
        candidate = decode(SolutionVersion, json.loads(path.read_text()))
        changed = candidate.artifacts[0]
        malformed = (
            replace(candidate, model=replace(candidate.model, name="task-agent-other-model")),
            replace(candidate, harness=replace(candidate.harness, name="changed")),
            replace(candidate, parent_solution_id=None),
            replace(candidate, artifacts=(replace(changed, parent_version_id=None), self.other)),
            replace(candidate, artifacts=(changed,)),
            replace(candidate, artifacts=(self.other, changed)),
            replace(candidate, artifacts=(changed, changed)),
            replace(candidate, artifacts=(self.product, self.other)),
            replace(
                candidate,
                artifacts=(replace(changed, content=self.product.content), self.other),
            ),
            replace(
                candidate,
                artifacts=(replace(changed, version_id=self.product.version_id), self.other),
            ),
        )
        calls = self.runtime.calls
        for index, invalid in enumerate(malformed):
            with self.subTest(index=index):
                write_json(path, invalid)
                self.service = self.make_service()
                await self.service.resume(run.run_id)
                failed = await self.service.tasks[run.run_id]
                self.assertEqual(failed.state, RunState.FAILED)
                self.assertIn("Artifact", failed.error or "")
                self.assertEqual(self.runtime.calls, calls)
        write_json(path, candidate)
        self.service = self.make_service()
        await self.service.resume(run.run_id)
        finished = await self.service.tasks[run.run_id]
        self.assertEqual(finished.state, RunState.SUCCEEDED, finished.error)
        self.assertEqual(self.runtime.calls, calls)
