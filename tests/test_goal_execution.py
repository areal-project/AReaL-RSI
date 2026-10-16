"""Goal execution against deterministic components, separate from real-model demos."""

from __future__ import annotations

import asyncio
import difflib
import json
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from areal_rsi.api.base import (
    BenchmarkSplit,
    ContentRef,
    EvaluationReport,
    EvolutionTarget,
    HarnessChange,
    JsonObject,
    SolutionVersion,
    Task,
    TaskResult,
    Trajectory,
    VerificationRequest,
    VerificationResult,
    VerifierSpec,
)
from areal_rsi.api.goal import GoalState
from areal_rsi.api.workflow import WorkflowContext
from areal_rsi.base.storage import (
    FileContentStore,
    FileMemory,
    FileTrajectoryStore,
    FileVersionManager,
    decode,
    new_id,
    now,
)
from areal_rsi.goals import GoalService
from areal_rsi.modules.benchmark import AccuracyEvaluation
from areal_rsi.modules.harness import LocalHarnessBuilder
from areal_rsi.workflows.service import LocalExperimentService


class FixtureBenchmark:
    name = "goal-fixture"

    async def list_tasks(self, split: BenchmarkSplit) -> Sequence[Task]:
        return [Task(split.value, {"question": split.value})]


class FixtureVerifier:
    def __init__(self, content: FileContentStore):
        self.content = content

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        value = json.loads(await self.content.read_text(request.result.output))
        correct = bool(value["correct"])
        return VerificationResult(
            request.task.task_id,
            request.result.trajectory.trajectory_id,
            correct,
            float(correct),
            {"required": float(correct)},
            "fixture",
        )


class FixtureResearch:
    agent_id = "research-agent-1"

    def __init__(self, app: Any, controls: Any):
        self.app, self.controls = app, controls

    async def propose(
        self,
        goal: str,
        target: EvolutionTarget,
        current: SolutionVersion,
        report: EvaluationReport,
        trajectories: ContentRef,
    ) -> HarnessChange:
        self.controls.proposals += 1
        if self.controls.failed_proposals:
            self.controls.failed_proposals -= 1
            raise RuntimeError("temporary fixture failure")
        if self.controls.dependency_phase == "propose":
            raise FileNotFoundError("research dependency unavailable")
        manifest = json.loads(await self.app.content.read_text(current.harness.content))["files"]
        original = await self.app.content.read_text(decode(ContentRef, manifest["prompt.md"]))
        patch = "".join(
            difflib.unified_diff(
                original.splitlines(True),
                [f"improved-{self.controls.proposals}\n"],
                fromfile="a/prompt.md",
                tofile="b/prompt.md",
            )
        )
        return HarnessChange(await self.app.content.put_text(patch), "fixture proposal")

    async def select(
        self, current_solution_id: str, candidates: Sequence[EvaluationReport]
    ) -> str | None:
        if self.controls.phase == "select":
            self.controls.reached.set()
            await self.controls.release.wait()
        return candidates[-1].solution_id


class FixtureRuntime:
    def __init__(self, app: Any, controls: Any):
        self.app, self.controls = app, controls

    async def run(
        self, solution: SolutionVersion, task: Task, *, config: JsonObject | None = None
    ) -> TaskResult:
        if self.controls.dependency_phase == "evaluate":
            raise FileNotFoundError("evaluation dependency unavailable")
        if self.controls.phase == "evaluate" and task.task_id == "train":
            self.controls.reached.set()
            await self.controls.release.wait()
        key = str((config or {})["execution_key"])
        self.controls.calls.append(key)
        manifest = json.loads(await self.app.content.read_text(solution.harness.content))["files"]
        prompt = await self.app.content.read_text(decode(ContentRef, manifest["prompt.md"]))
        correct = (
            "improved" in prompt or self.controls.baseline_pass
        ) and not self.controls.never_pass
        if task.task_id == "final" and self.controls.final_fail:
            correct = False
        trajectory = await self.app.trajectories.save(
            Trajectory(
                new_id("trajectory"),
                "fixture",
                await self.app.content.put_json({"turns": []}),
                {},
                now(),
            )
        )
        return TaskResult(await self.app.content.put_json({"correct": correct}), trajectory)


class FixtureBuilder(LocalHarnessBuilder):
    def __init__(self, content: FileContentStore, root: Path, controls: Any):
        super().__init__(content, root)
        self.controls = controls

    async def build(self, base: Any, change: HarnessChange) -> ContentRef:
        if self.controls.phase == "build":
            self.controls.reached.set()
            await self.controls.release.wait()
        return await super().build(base, change)


class FixtureApp:
    baseline: SolutionVersion
    service: LocalExperimentService

    def __init__(self, root: Path, controls: Any):
        self.root, self.controls = root, controls
        self.content = FileContentStore(root / "content")
        self.versions = FileVersionManager(root / "versions")
        self.trajectories = FileTrajectoryStore(root / "trajectories", self.content)
        self.runtime_settings = SimpleNamespace(
            identity="fixture-runtime", validate_artifacts=lambda: None
        )
        self.rsi = SimpleNamespace(workflow_name="harness-evolution")
        self.agent_runtimes = {"research-agent-1": SimpleNamespace(selection={"kind": "dsh"})}

    def model_plan_config(self) -> JsonObject:
        return {"runtime_config_digest": "fixture-runtime"}

    async def bind_plan(self, plan: Any) -> None:
        assert plan.config["runtime_config_digest"] == "fixture-runtime"

    async def close(self) -> None:
        for run_id in self.service.tasks:
            await self.service.stop(run_id)


def setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, seconds: float = 10
) -> tuple[GoalService, str, Any]:
    controls = SimpleNamespace(
        phase=None,
        reached=asyncio.Event(),
        release=asyncio.Event(),
        calls=[],
        proposals=0,
        failed_proposals=0,
        never_pass=False,
        final_fail=False,
        baseline_pass=False,
        dependency_phase=None,
    )

    async def create(root: Path, emit: Any, *, frozen: Any = None, **kwargs: Any) -> FixtureApp:
        app = FixtureApp(root, controls)
        if frozen:
            app.baseline = decode(SolutionVersion, frozen["baseline"])
        else:
            source = root / "source"
            source.mkdir(exist_ok=True)
            (source / "prompt.md").write_text("baseline\n")
            (source / "mcp.json").write_text('{"servers": []}')
            app.baseline = await app.versions.create_solution(
                await app.versions.create_model("fixed", await app.content.put_text("fixed")),
                await app.versions.create_harness("fixture", await app.content.snapshot(source)),
            )
        context = WorkflowContext(
            app.content,
            FileMemory(root / "memory"),
            app.trajectories,
            app.versions,
            FixtureRuntime(app, controls),
            app.rsi,
            (FixtureResearch(app, controls),),
            FixtureBenchmark(),
            FixtureVerifier(app.content),
            VerifierSpec("fixture-v1", "injected"),
            AccuracyEvaluation(),
            FixtureBuilder(app.content, root / "builds", controls),
        )
        app.service = LocalExperimentService(root, context, emit)
        return app

    monkeypatch.setattr("areal_rsi.app.LocalApp.create", create)
    service = GoalService(tmp_path)
    goal = service.draft(
        {
            "description": "fixture",
            "target": "harness",
            "benchmark": "goal-fixture",
            "acceptance": {
                "metrics": [{"metric": "accuracy", "threshold": 1.0}],
                "required_checks": ["required"],
                "verifier_version": "fixture-v1",
            },
            "budget": {"max_iterations": 1, "max_seconds": seconds, "max_retries": 0},
            "allowed_paths": ["prompt.md"],
            "invariants": ["fixed model"],
            "skill_paths": [],
        }
    )
    return service, goal.goal_id, controls


def test_success_requires_independent_final_and_records_frozen_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        service, goal_id, controls = setup(tmp_path, monkeypatch)
        goal = await service.confirm(goal_id, source="fixture-test")
        outcome = await service.execute(goal_id, goal.revision)
        assert outcome.state == GoalState.ACHIEVED and outcome.acceptance_report
        assert controls.proposals == 1
        assert len([key for key in controls.calls if "/final/" in key]) == 2
        report = json.loads(await service.content.read_text(outcome.acceptance_report))
        assert report["candidate"]["solution_id"] == outcome.solution_id
        assert report["baseline"]["solution_id"] == goal.frozen_identity["baseline_solution_id"]

    asyncio.run(scenario())


def test_final_failure_closes_search(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        service, goal_id, controls = setup(tmp_path, monkeypatch)
        controls.final_fail = True
        goal = await service.confirm(goal_id, source="fixture-test")
        outcome = await service.execute(goal_id, goal.revision)
        assert (
            outcome.state == GoalState.FAILED
            and outcome.stop_reason == "independent_acceptance_failed"
        )
        assert controls.proposals == 1
        with pytest.raises(ValueError, match="new revision"):
            service.launch(goal_id, resume=True)

    asyncio.run(scenario())


def test_unused_seed_or_new_ids_cannot_reuse_any_earlier_final_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        service, goal_id, controls = setup(tmp_path, monkeypatch)
        controls.final_fail = True
        goal = await service.confirm(goal_id, source="fixture-test")
        assert (await service.execute(goal_id, goal.revision)).state == GoalState.FAILED
        service.answer(goal_id, {"assets": {"benchmark_seed": 99}})

        async def renamed_tasks(self: FixtureBenchmark, split: BenchmarkSplit) -> Sequence[Task]:
            return [Task("renamed-" + split.value, {"question": split.value})]

        monkeypatch.setattr(FixtureBenchmark, "list_tasks", renamed_tasks)
        # Reconfirming uses actual inputs, despite changed IDs, seed and an
        # intervening draft revision that itself has not exposed FINAL.
        service.answer(goal_id, {"assets": {"benchmark_seed": 100}})
        with pytest.raises(ValueError, match="already exposed"):
            await service.confirm(goal_id, source="fixture-test")
        assert service.get(goal_id).confirmed_at is None

        async def fresh_tasks(self: FixtureBenchmark, split: BenchmarkSplit) -> Sequence[Task]:
            return [Task("renamed-" + split.value, {"question": "fresh-" + split.value})]

        monkeypatch.setattr(FixtureBenchmark, "list_tasks", fresh_tasks)
        assert (await service.confirm(goal_id, source="fixture-test")).confirmed_at

    asyncio.run(scenario())


def test_unmet_validation_gain_continues_research_until_the_iteration_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        service, goal_id, controls = setup(tmp_path, monkeypatch)
        controls.baseline_pass = True
        request = service.get(goal_id).request
        previous = request["acceptance"]
        assert isinstance(previous, dict)
        acceptance = dict(previous)
        acceptance["metrics"] = [
            {"metric": "accuracy", "threshold": 1.0},
            {"metric": "gain_accuracy", "threshold": 0.1},
        ]
        service.answer(goal_id, {"acceptance": acceptance, "budget": {"max_iterations": 2}})
        goal = await service.confirm(goal_id, source="fixture-test")
        outcome = await service.execute(goal_id, goal.revision)
        assert controls.proposals == 2
        assert outcome.state == GoalState.BUDGET_EXHAUSTED
        assert outcome.stop_reason == "iteration_budget_exhausted"
        assert outcome.acceptance_report is None
        assert not any("/final/" in key for key in controls.calls)

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["evaluate", "build", "select"])
def test_pause_resume_keeps_completed_checkpoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    async def scenario() -> None:
        service, goal_id, controls = setup(tmp_path, monkeypatch)
        controls.phase = phase
        goal = await service.confirm(goal_id, source="fixture-test")
        task = asyncio.create_task(service.execute(goal_id, goal.revision))
        await asyncio.wait_for(controls.reached.wait(), 5)
        service.request(goal_id, "pause")
        paused = await asyncio.wait_for(task, 5)
        assert paused.state == GoalState.PAUSED
        completed_calls = list(controls.calls)
        controls.phase = None
        (service.control / f"{service.key(goal)}-request.json").unlink()
        outcome = await service.execute(goal_id, goal.revision)
        assert outcome.state == GoalState.ACHIEVED
        assert controls.calls[: len(completed_calls)] == completed_calls
        assert len(controls.calls) == len(set(controls.calls))
        assert controls.proposals == 1

    asyncio.run(scenario())


def test_timeout_is_budget_exhausted_and_does_not_dispatch_final(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        service, goal_id, controls = setup(tmp_path, monkeypatch, seconds=0.3)
        controls.phase = "evaluate"
        goal = await service.confirm(goal_id, source="fixture-test")
        outcome = await asyncio.wait_for(service.execute(goal_id, goal.revision), 4)
        assert outcome.state == GoalState.BUDGET_EXHAUSTED
        assert outcome.stop_reason == "time_budget_exhausted"
        assert not any("/final/" in key for key in controls.calls)

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["evaluate", "propose"])
def test_missing_dependency_is_blocked_without_exhausting_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    async def scenario() -> None:
        service, goal_id, controls = setup(tmp_path, monkeypatch)
        controls.dependency_phase = phase
        goal = await service.confirm(goal_id, source="fixture-test")
        outcome = await service.execute(goal_id, goal.revision)
        assert outcome.state == GoalState.BLOCKED
        assert "restore it and resume" in outcome.stop_reason
        assert outcome.attempts == 1
        controls.dependency_phase = None
        resumed = await service.execute(goal_id, goal.revision)
        assert resumed.state == GoalState.ACHIEVED

    asyncio.run(scenario())


def test_retry_retains_failed_attempt_details_after_eventual_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        service, goal_id, controls = setup(tmp_path, monkeypatch)
        controls.failed_proposals = 1
        service.answer(goal_id, {"budget": {"max_retries": 1}})
        goal = await service.confirm(goal_id, source="fixture-test")
        outcome = await service.execute(goal_id, goal.revision)
        assert outcome.state == GoalState.ACHIEVED and outcome.attempts == 2
        history = service.status(goal_id)["retry_history"]
        failure = next(event for event in history if event["type"] == "attempt_failed")
        assert failure["attempt"] == 1
        assert "temporary fixture failure" in failure["error"]
        assert any(event["type"] == "retry" for event in history)
        assert len(controls.calls) == len(set(controls.calls))

    asyncio.run(scenario())
