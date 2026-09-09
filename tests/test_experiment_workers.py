"""独立 worker 的离线进程测试；不打开模型 Session，也不连接外部服务。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from areal_rsi.agents.rsi import DefaultRsiAgent
from areal_rsi.api.base import (
    BenchmarkSplit,
    ExperimentPlan,
    ExperimentRun,
    JsonObject,
    RunState,
    SolutionVersion,
    Task,
    VerifierSpec,
)
from areal_rsi.api.workflow import WorkflowContext
from areal_rsi.base.storage import (
    FileContentStore,
    FileMemory,
    FileRecords,
    FileTrajectoryStore,
    FileVersionManager,
    decode,
    new_id,
    now,
    write_json,
)
from areal_rsi.ui.experiments import ExperimentController, _locked, run_worker
from areal_rsi.workflows.service import LocalExperimentService


class WorkerBenchmarkFixture:
    name = "worker-fixture-benchmark"

    async def list_tasks(self, split: BenchmarkSplit) -> tuple[Task, ...]:
        return (Task(split.value, "offline"),)


class WorkerWorkflowFixture:
    name = "worker-fixture"

    def __init__(
        self, root: Path, records: FileRecords, run: ExperimentRun, emit: Callable[[str], None]
    ):
        self.root, self.records, self.state, self.emit = root.parent, records, run, emit

    async def run(self, context: WorkflowContext, plan: ExperimentPlan) -> ExperimentRun:
        self.state.state = RunState.RUNNING
        self.records.save(self.state.run_id, self.state, immutable=False)
        write_json(
            self.root / f"{self.state.run_id}-entered.json",
            {
                "pid": os.getpid(),
                "mode": os.environ.get("RSI_MODE"),
                "runtime_config": os.environ.get("RSI_RUNTIME_CONFIG"),
            },
        )
        self.emit("offline worker entered")
        if plan.goal == "fixture failure":
            raise RuntimeError("PRIVATE_FAILURE_DETAILS")
        async with asyncio.timeout(15):
            while not (self.root / "fixture-release.json").exists():
                await asyncio.sleep(0.02)
        self.state.state, self.state.finished_at = RunState.SUCCEEDED, now()
        return self.state


class WorkerAppFixture:
    baseline: SolutionVersion
    rsi: DefaultRsiAgent
    service: LocalExperimentService

    def model_plan_config(self) -> JsonObject:
        ids = [
            "rsi-agent",
            *(agent.agent_id for agent in self.service.context.research_agents),
        ]
        models: JsonObject = {
            agent_id: {"model": self.rsi.model.name, "version_id": self.rsi.model.version_id}
            for agent_id in ids
        }
        return {"agent_models": models, "target_model_name": self.baseline.model.name}

    async def bind_plan(self, plan: ExperimentPlan) -> None:
        self.service.validate(plan)
        if plan.baseline_solution_id != self.baseline.solution_id:
            raise ValueError("fixture baseline mismatch")

    async def close(self) -> None:
        for run_id, task in self.service.tasks.items():
            if not task.done():
                await self.service.stop(run_id)


async def fixture_app(root: Path, emit: Callable[[str], None]) -> WorkerAppFixture:
    if (root / "fixture-delay-app.json").exists():
        write_json(root / "fixture-app-waiting.json", {})
        while not (root / "fixture-app-release.json").exists():
            await asyncio.sleep(0.02)
    app = WorkerAppFixture()
    content = FileContentStore(root / "content")
    versions = FileVersionManager(root / "versions")
    fixture_records = FileRecords(root / "fixture-records")
    baseline = fixture_records.get("baseline", SolutionVersion)
    if baseline is None:
        baseline = await versions.create_solution(
            await versions.create_model(
                "shared-agent-fixture-model", await content.put_text("model")
            ),
            await versions.create_harness("offline-harness", await content.put_text("harness")),
        )
        fixture_records.save("baseline", baseline)
    app.baseline = baseline
    research_agent_count = 2 if (root / "fixture-change-count.json").exists() else 1
    app.rsi = DefaultRsiAgent(
        Mock(),
        baseline.model,
        baseline.solution_id,
        WorkerBenchmarkFixture.name,
        workflow_name="worker-fixture",
        research_agent_count=research_agent_count,
    )
    researchers = tuple(
        Mock(agent_id=f"offline-research-{index}") for index in range(1, research_agent_count + 1)
    )
    context = WorkflowContext(
        content,
        FileMemory(root / "memory"),
        FileTrajectoryStore(root / "traces", content),
        versions,
        Mock(),
        app.rsi,
        researchers,
        WorkerBenchmarkFixture(),
        Mock(),
        VerifierSpec("worker-fixture-verifier", "injected"),
        Mock(),
    )
    app.service = LocalExperimentService(
        root, context, emit, workflows={"worker-fixture": WorkerWorkflowFixture}
    )
    return app


class ExperimentWorkersTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.controller = self.make_controller()
        self.controllers = [self.controller]
        self.external_runs: list[str] = []

    def make_controller(self) -> ExperimentController:
        return ExperimentController(
            self.root,
            app_factory=fixture_app,
            worker_command=(sys.executable, str(Path(__file__).resolve())),
        )

    async def asyncTearDown(self) -> None:
        for run_id in self.external_runs:
            await self.controller.stop(run_id)
            async with asyncio.timeout(5):
                while (await self.controller.status(run_id))["worker"]["active"]:
                    await asyncio.sleep(0.03)
        for controller in self.controllers:
            for child in list(controller._children.values()):
                if child.poll() is None:
                    # 仍是本测试尚未回收的直接子进程，其 PID 不会在 wait 前被复用。
                    child.send_signal(signal.SIGTERM)
                await asyncio.to_thread(child.wait, timeout=5)

    async def wait_state(self, run_id: str, state: str) -> dict[str, object]:
        async with asyncio.timeout(5):
            while True:
                result = await self.controller.status(run_id)
                if result["state"] == state:
                    return result
                await asyncio.sleep(0.03)

    async def test_plan_uses_fixed_configuration_without_chat_or_worker(self) -> None:
        with patch.object(DefaultRsiAgent, "chat", new=AsyncMock()) as chat:
            result = await self.controller.plan("user goal", 2)
        chat.assert_not_awaited()
        self.assertTrue(result["confirmation_required"])
        self.assertEqual(result["plan"]["workflow"], "worker-fixture")
        self.assertEqual(result["plan"]["benchmark"], WorkerBenchmarkFixture.name)
        self.assertEqual(result["plan"]["research_agent_count"], 1)
        self.assertEqual(
            result["plan"]["config"]["agent_models"]["rsi-agent"]["model"],
            "shared-agent-fixture-model",
        )
        self.assertEqual(
            result["plan"]["config"]["target_model_name"], "shared-agent-fixture-model"
        )
        self.assertNotIn("research_directions", result["plan"]["config"])
        self.assertEqual(await self.controller.status(), {"runs": []})
        self.assertEqual(self.controller._children, {})
        path = self.root / "experiment-plans" / f"{result['plan_id']}.json"
        self.assertEqual(json.loads(path.read_text())["plan"], result["plan"])

    async def test_start_requires_strict_confirmation_and_is_idempotent(self) -> None:
        plan = await self.controller.plan("user goal", 2)
        with self.assertRaisesRegex(ValueError, "confirmed"):
            await self.controller.start(plan["plan_id"])
        with self.assertRaisesRegex(ValueError, "confirmed"):
            await self.controller.start(plan["plan_id"], confirmed=1)  # type: ignore[arg-type]
        first = await self.controller.start(plan["plan_id"], confirmed=True)
        other = self.make_controller()
        self.controllers.append(other)
        second = await other.start(plan["plan_id"], confirmed=True)
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertEqual(first["worker"]["pid"], second["worker"]["pid"])
        self.assertEqual(len(list((self.root / "run-states").glob("*.json"))), 1)
        self.assertEqual(os.getsid(first["worker"]["pid"]), first["worker"]["pid"])
        self.assertTrue(second["worker"]["active"])
        stopped = await other.stop(first["run_id"])
        self.assertTrue(stopped["stop_requested"])
        await self.wait_state(first["run_id"], "cancelled")

    async def test_detached_worker_survives_controller_and_can_resume(self) -> None:
        plan = await self.controller.plan("resume fixture", 3)
        started = await self.controller.start(plan["plan_id"], confirmed=True)
        run_id, original_pid = started["run_id"], started["worker"]["pid"]
        original_child = self.controller._children[run_id]
        await self.wait_state(run_id, "running")
        other = self.make_controller()
        self.controllers.append(other)
        self.assertTrue((await other.status(run_id))["worker"]["active"])
        self.assertTrue((await other.stop(run_id))["stop_requested"])
        await self.wait_state(run_id, "cancelled")
        await asyncio.to_thread(original_child.wait, timeout=5)
        with self.assertRaisesRegex(ValueError, "confirmed"):
            await other.resume(run_id)
        resumed = await other.resume(run_id, confirmed=True)
        resumed_child = other._children[run_id]
        self.assertEqual(resumed["run_id"], run_id)
        self.assertNotEqual(resumed["worker"]["pid"], original_pid)
        await self.wait_state(run_id, "running")
        write_json(self.root / "fixture-release.json", {})
        finished = await self.wait_state(run_id, "succeeded")
        self.assertEqual(finished["max_iterations"], 3)
        self.assertIn(
            "offline worker entered", (self.root / "workers" / f"{run_id}.log").read_text()
        )
        await asyncio.to_thread(resumed_child.wait, timeout=5)
        same = await other.start(plan["plan_id"], confirmed=True)
        self.assertEqual(same["run_id"], run_id)
        self.assertEqual(same["state"], "succeeded")

    async def test_worker_survives_actual_launcher_process_exit(self) -> None:
        launcher = await asyncio.create_subprocess_exec(
            sys.executable,
            str(Path(__file__).resolve()),
            "--launcher-root",
            str(self.root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        output, error = await asyncio.wait_for(launcher.communicate(), timeout=5)
        self.assertEqual(launcher.returncode, 0, error.decode())
        started = json.loads(output)
        run_id = started["run_id"]
        self.external_runs.append(run_id)
        await self.wait_state(run_id, "running")
        self.assertTrue((await self.controller.status(run_id))["worker"]["active"])
        self.assertTrue((await self.controller.stop(run_id))["stop_requested"])
        await self.wait_state(run_id, "cancelled")

    async def test_stop_rejects_pid_identity_mismatch_without_signalling(self) -> None:
        plan = await self.controller.plan("identity fixture", 1)
        started = await self.controller.start(plan["plan_id"], confirmed=True)
        run_id = started["run_id"]
        await self.wait_state(run_id, "running")
        path = self.root / "workers" / f"{run_id}.json"
        original = json.loads(path.read_text())
        write_json(path, {**original, "identity": "stale-process-generation"})
        try:
            with patch("os.kill") as kill, self.assertRaisesRegex(ValueError, "PID"):
                await self.controller.stop(run_id)
            kill.assert_not_called()
        finally:
            write_json(path, original)
        self.assertTrue((await self.controller.stop(run_id))["stop_requested"])
        await self.wait_state(run_id, "cancelled")

    async def test_unowned_run_and_execution_lock_are_respected(self) -> None:
        plan = await self.controller.plan("unowned fixture", 1)
        parsed = decode(ExperimentPlan, plan["plan"])
        run = ExperimentRun(new_id("run"), parsed, RunState.PLANNED, parsed.baseline_solution_id)
        self.controller.runs.save(run.run_id, run)
        with self.assertRaisesRegex(ValueError, "归属"):
            await self.controller.stop(run.run_id)
        with (
            _locked(self.root / "locks" / f"{run.run_id}.lock"),
            self.assertRaisesRegex(ValueError, "另一个进程"),
        ):
            await self.controller.resume(run.run_id, confirmed=True)
        self.assertFalse((await self.controller.status(run.run_id))["worker"]["active"])

    async def test_worker_losing_execution_lock_preserves_legacy_run(self) -> None:
        plan = await self.controller.plan("legacy race fixture", 1)
        legacy = await fixture_app(self.root, lambda _: None)
        write_json(self.root / "fixture-delay-app.json", {})
        started = await self.controller.start(plan["plan_id"], confirmed=True)
        run_id = started["run_id"]
        child = self.controller._children[run_id]
        try:
            async with asyncio.timeout(5):
                while not (self.root / "fixture-app-waiting.json").exists():
                    await asyncio.sleep(0.02)
            await legacy.service.resume(run_id)
            await self.wait_state(run_id, "running")
            state_path = self.root / "run-states" / f"{run_id}.json"
            original = state_path.read_bytes()
            write_json(self.root / "fixture-app-release.json", {})
            self.assertEqual(await asyncio.to_thread(child.wait, timeout=5), 1)
            self.assertEqual(state_path.read_bytes(), original)
            status = await self.controller.status(run_id)
            self.assertEqual(status["worker"]["status"], "not_owner")
            self.assertEqual(status["state"], "running")
            self.assertFalse((await self.controller.stop(run_id))["stop_requested"])
        finally:
            await legacy.close()

    async def test_status_is_bounded_and_hides_raw_failure(self) -> None:
        plan = await self.controller.plan("fixture failure", 1)
        started = await self.controller.start(plan["plan_id"], confirmed=True)
        failed = await self.wait_state(started["run_id"], "failed")
        self.assertNotIn("PRIVATE_FAILURE_DETAILS", json.dumps(failed))
        self.assertNotIn("research_agents", failed)
        parsed = decode(ExperimentPlan, plan["plan"])
        parsed.goal = "x" * 1000
        for _ in range(23):
            run = ExperimentRun(
                new_id("run"), parsed, RunState.PLANNED, parsed.baseline_solution_id
            )
            self.controller.runs.save(run.run_id, run)
        recent = await self.controller.status()
        self.assertEqual(len(recent["runs"]), 20)
        self.assertTrue(all(len(run["goal"]) <= 500 for run in recent["runs"]))

    async def test_worker_rejects_configuration_changed_after_confirmation(self) -> None:
        plan = await self.controller.plan("fixed configuration fixture", 1)
        write_json(self.root / "fixture-change-count.json", {})
        started = await self.controller.start(plan["plan_id"], confirmed=True)
        run_id = started["run_id"]
        await self.wait_state(run_id, "failed")
        self.assertFalse((self.root / f"{run_id}-entered.json").exists())
        self.assertIn("ResearchAgent 数量与装配不匹配", self.controller._run(run_id).error or "")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--launcher-root", type=Path)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--token")
    parser.add_argument("--lock-fd", type=int)
    args = parser.parse_args()
    if args.launcher_root:

        async def launch() -> None:
            controller = ExperimentController(
                args.launcher_root,
                app_factory=fixture_app,
                worker_command=(sys.executable, str(Path(__file__).resolve())),
            )
            plan = await controller.plan("detached launcher fixture", 1)
            print(json.dumps(await controller.start(plan["plan_id"], confirmed=True)), flush=True)

        asyncio.run(launch())
        raise SystemExit(0)
    raise SystemExit(
        asyncio.run(
            run_worker(args.root, args.run_id, args.token, args.lock_fd, app_factory=fixture_app)
        )
    )
