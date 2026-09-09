"""Provider-neutral remote assembly, selection and status projection."""

from __future__ import annotations

import json
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from unittest.mock import patch

from areal_rsi.api.base import (
    BenchmarkSplit,
    ContentRef,
    EvaluationReport,
    EvolutionTarget,
    ExperimentPlan,
    HarnessChange,
    HarnessVersion,
    JsonObject,
    ModelVersion,
    RunState,
    SolutionVersion,
    Task,
    TaskResult,
    Trajectory,
    VerificationRequest,
    VerificationResult,
    VerifierSpec,
)
from areal_rsi.api.modules import ConfiguredAgentRuntime
from areal_rsi.app import LocalApp, create_app
from areal_rsi.base.storage import decode, new_id, now, write_json
from areal_rsi.config import ModelConfigurationError
from areal_rsi.modules.benchmark import AccuracyEvaluation
from areal_rsi.modules.dsh_config import write_profile_patch
from areal_rsi.modules.remote.base import RemoteBackend, RemoteContext, RemoteJob
from areal_rsi.modules.remote.registry import (
    PROVIDERS,
    RemoteSettings,
    remote_environment_names,
    stored_backend,
)
from areal_rsi.ui.web import RunReader


class DummyComponents:
    name = "dummy-benchmark"

    def __init__(self, context: RemoteContext):
        self.context = context

    async def list_tasks(self, split: BenchmarkSplit) -> Sequence[Task]:
        return [Task("task-" + split.value, "public question", {})]

    async def run(
        self, solution: SolutionVersion, task: Task, *, config: JsonObject | None = None
    ) -> TaskResult:
        output = await self.context.content.put_json({"answer": "fixture"})
        ref = await self.context.trajectories.save(
            Trajectory(
                new_id("trajectory"),
                "dummy-task",
                output,
                {},
                now(),
                task_id=task.task_id,
                solution_id=solution.solution_id,
            )
        )
        return TaskResult(output, ref)

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        return VerificationResult(
            request.task.task_id, request.result.trajectory.trajectory_id, True, 1.0, {}, "fixture"
        )

    async def build(self, base: HarnessVersion, change: HarnessChange) -> ContentRef:
        return change.patch


class DummyResearchAgent:
    def __init__(self, agent_id: str, context: RemoteContext):
        self.agent_id, self.context = agent_id, context

    async def propose(
        self,
        goal: str,
        target: EvolutionTarget,
        current: SolutionVersion,
        report: EvaluationReport,
        trajectories: ContentRef,
    ) -> HarnessChange:
        assert report.split == BenchmarkSplit.TRAIN
        return HarnessChange(await self.context.content.put_text("dummy candidate"), "fixture")

    async def select(
        self, current_solution_id: str, candidates: Sequence[EvaluationReport]
    ) -> str | None:
        return candidates[-1].solution_id


class DummyProvider:
    environment_names = ("DUMMY_REMOTE_TOKEN",)

    def __init__(self) -> None:
        self.create_count = 0

    async def create(self, context: RemoteContext, config_path: Path) -> RemoteBackend:
        assert stored_backend(context.root) == "dummy"
        self.create_count += 1
        assert json.loads(config_path.read_text()) == {"dummy_endpoint": "fixture"}
        path = context.root / "baseline.json"
        if path.exists():
            baseline = decode(SolutionVersion, json.loads(path.read_text()))
        else:
            model = await context.versions.create_model(
                "dummy-target", await context.content.put_json({"provider": "dummy"})
            )
            harness = await context.versions.create_harness(
                "dummy-harness", await context.content.put_text("dummy configuration")
            )
            baseline = await context.versions.create_solution(model, harness)
            write_json(path, baseline)
        components = DummyComponents(context)

        def researcher(
            agent_id: str, runtime: ConfiguredAgentRuntime, model: ModelVersion
        ) -> DummyResearchAgent:
            return DummyResearchAgent(agent_id, context)

        return RemoteBackend(
            baseline,
            components,
            components,
            components,
            VerifierSpec("dummy", "injected"),
            AccuracyEvaluation(),
            components,
            researcher,
            "Dummy remote provider",
        )

    def read_jobs(self, root: Path, run_id: str) -> Sequence[RemoteJob]:
        return [
            RemoteJob(
                "dummy-job", "DONE", {"total": 1, "done": 1}, "https://dummy.example/jobs/dummy-job"
            )
        ]


class RemoteProviderTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / "dummy.json"
        self.config.write_text('{"dummy_endpoint":"fixture"}')
        self.provider = DummyProvider()
        self.enterContext(patch.dict(PROVIDERS, {"dummy": self.provider}))
        self.enterContext(
            patch.dict(
                "os.environ",
                {
                    "RSI_MODE": "remote",
                    "RSI_REMOTE_BACKEND": "dummy",
                    "RSI_REMOTE_CONFIG": str(self.config),
                    "RSI_TEACHER_MODEL": "research-model",
                    "RSI_TEACHER_BASE_URL": "http://localhost:1/v1",
                    "DUMMY_REMOTE_TOKEN": "dummy-private-token",
                },
                clear=True,
            )
        )

    async def test_other_provider_runs_workflow_and_supplies_web_jobs(self) -> None:
        root = self.root / "data"
        app = await create_app(root, lambda _: None)
        try:
            plan = ExperimentPlan(
                "provider test",
                "harness-evolution",
                "dummy-benchmark",
                app.baseline.solution_id,
                EvolutionTarget.HARNESS,
                1,
                stop_when_goal_reached=False,
                config=app.model_plan_config(),
            )
            self.assertEqual(plan.config["remote_backend"], "dummy")
            await app.bind_plan(plan)
            run = await app.service.start(plan)
            result = await app.service.tasks[run.run_id]
            self.assertEqual(result.state, RunState.SUCCEEDED, result.error)
            self.assertEqual(len(result.research_agents[0].iterations), 1)
            view = RunReader(root).detail(run.run_id)
            self.assertEqual(view["remote_jobs"][0]["url"], "https://dummy.example/jobs/dummy-job")
            plan.config["remote_backend"] = "other"
            with self.assertRaisesRegex(ModelConfigurationError, "backend"):
                await app.bind_plan(plan)
        finally:
            await app.close()
        self.assertEqual(self.provider.create_count, 1)
        with self.assertRaisesRegex(ValueError, "remote mode"):
            await LocalApp.create(root, lambda _: None)
        with (
            patch.dict("os.environ", {"RSI_REMOTE_BACKEND": "envarena"}),
            self.assertRaisesRegex(ValueError, "其他 remote backend"),
        ):
            await create_app(root, lambda _: None)

    def test_provider_environment_is_forwarded_without_credential_values(self) -> None:
        self.assertEqual(remote_environment_names(), ("DUMMY_REMOTE_TOKEN",))
        path = write_profile_patch(self.root / "data", self.root, "model")
        text = path.read_text()
        self.assertIn("DUMMY_REMOTE_TOKEN", text)
        self.assertNotIn("dummy-private-token", text)
        self.assertNotIn("ARENA_OPENAPI_TOKEN", text)
        with patch.dict("os.environ", {"RSI_MODE": "local", "RSI_REMOTE_BACKEND": "unknown"}):
            self.assertEqual(remote_environment_names(), ())

    def test_generic_config_and_legacy_arena_alias(self) -> None:
        self.assertEqual(RemoteSettings.from_environment().backend, "dummy")
        with patch.dict("os.environ", {"RSI_ARENA_CONFIG": str(self.config)}, clear=True):
            self.assertEqual(RemoteSettings.from_environment().config_path, self.config.resolve())
        with patch.dict(
            "os.environ",
            {
                "RSI_REMOTE_BACKEND": "dummy",
                "RSI_ARENA_CONFIG": str(self.config),
            },
            clear=True,
        ), self.assertRaisesRegex(ValueError, "RSI_REMOTE_CONFIG"):
            RemoteSettings.from_environment()
        with (
            patch.dict("os.environ", {"RSI_REMOTE_BACKEND": "unknown"}),
            self.assertRaisesRegex(ValueError, "未知 remote backend"),
        ):
            RemoteSettings.from_environment()

    def test_remote_job_rejects_unsafe_links(self) -> None:
        for url in ("javascript:alert(1)", "https://user:secret@example.com/job"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                RemoteJob("job", "running", {}, url)
