"""Run ownership, generic research records and non-destructive legacy reads."""

from __future__ import annotations

import json
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

from areal_rsi.agents.research import DefaultResearchAgent
from areal_rsi.api.base import (
    AgentState,
    EvolutionTarget,
    ExperimentPlan,
    ExperimentRun,
    IterationState,
    RecordRef,
    ResearchAgentState,
    ResearchIteration,
    RunState,
)
from areal_rsi.api.workflow import WorkflowContext
from areal_rsi.app import LocalApp
from areal_rsi.base.runs import FileRunStore
from areal_rsi.base.storage import FileMemory, encode, write_json
from areal_rsi.ui.web import RunReader


def plan() -> ExperimentPlan:
    return ExperimentPlan(
        "Find out what is needed next", "probe", "arithmetic-v1", None, EvolutionTarget.HARNESS, 2
    )


class RunStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = FileRunStore(self.root)

    def test_generic_nodes_round_trip_without_products_or_evaluations(self) -> None:
        paths = self.store.paths("run_a")
        write_json(paths.root / "request.json", {"instruction": "Answer the question"})
        write_json(paths.root / "answer.json", {"answer": "More information is needed"})
        first = ResearchIteration(
            "one",
            "run_a",
            "agent1",
            paths.reference(paths.root / "request.json"),
            IterationState.COMPLETED,
            report_ref=paths.reference(paths.root / "answer.json"),
        )
        second = ResearchIteration(
            "two",
            "run_a",
            "agent1",
            first.assignment_ref,
            parent_iteration_id="one",
            references=(first.report_ref,) if first.report_ref else (),
        )
        agent = ResearchAgentState(
            "agent1",
            AgentState.WAITING,
            (first, second),
            current_iteration_id="two",
            wait_reason="rsi_reply",
        )
        run = ExperimentRun("run_a", plan(), RunState.RUNNING, research_agents=(agent,))
        self.store.save(run.run_id, run)
        restored = self.store.get(run.run_id, ExperimentRun)
        self.assertEqual(restored, run)
        self.assertIsNone(restored.best_solution_id if restored else "missing")
        # Child bodies have a single on-disk owner; parents contain IDs.
        self.assertEqual(json.loads(paths.state.read_text())["research_agents"], ["agent1"])
        self.assertEqual(
            json.loads((paths.agent("agent1") / "agent.json").read_text())["iterations"],
            ["one", "two"],
        )
        assert first.report_ref is not None
        self.assertEqual(
            self.store.read_json(first.report_ref), {"answer": "More information is needed"}
        )
        view = RunReader(self.root).detail(run.run_id)
        self.assertEqual(view["agents"][0]["completed_iterations"], 1)
        self.assertIsNone(view["best_score"])
        self.assertNotIn("More information", json.dumps(view))

    def test_foreign_node_is_rejected_before_writing(self) -> None:
        foreign = ResearchIteration(
            "one", "run_other", "agent1", RecordRef("run://run_other/x.json")
        )
        run = ExperimentRun(
            "run_a",
            plan(),
            RunState.RUNNING,
            research_agents=(ResearchAgentState("agent1", iterations=(foreign,)),),
        )
        with self.assertRaisesRegex(ValueError, "different Run"):
            self.store.save(run.run_id, run)
        self.assertFalse(self.store.paths(run.run_id).root.exists())

    def test_record_reference_cannot_escape_the_run(self) -> None:
        for uri in (
            "run://run_a/../secret.json",
            "run://run_a/%2e%2e/secret.json",
            "file:///etc/passwd",
            "run://run_a/x.json?path=/etc/passwd",
        ):
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                self.store.read_json(RecordRef(uri))
        paths = self.store.paths("run_a")
        paths.root.mkdir(parents=True)
        (paths.root / "escape").symlink_to(self.root)
        with self.assertRaisesRegex(ValueError, "escapes"):
            self.store.read_json(RecordRef("run://run_a/escape/secret.json"))

    def test_record_reference_preserves_uri_special_characters(self) -> None:
        paths = self.store.paths("run_a")
        record = paths.root / "结果 #1?.json"
        write_json(record, {"answer": 42})
        self.assertEqual(self.store.read_json(paths.reference(record)), {"answer": 42})

    def test_legacy_read_and_save_preserve_source_records_and_lock_paths(self) -> None:
        paths = self.store.paths("old_run")
        old_step = {
            "iteration": 1,
            "started_at": "t1",
            "finished_at": "t2",
            "result": {"source": "original record"},
        }
        raw = encode(ExperimentRun("old_run", plan(), RunState.FAILED))
        raw["research_agents"] = [
            {
                "agent_id": "agent1",
                "state": "cancelled",
                "iterations": [old_step],
                "error": None,
            }
        ]
        write_json(paths.legacy_state, raw)
        original = paths.legacy_state.read_bytes()
        run = self.store.get("old_run", ExperimentRun)
        assert run is not None
        self.assertFalse(paths.state.exists())
        self.assertEqual(run.research_agents[0].state, AgentState.PAUSED)
        step = run.research_agents[0].iterations[0]
        assert step.report_ref is not None
        self.assertEqual(self.store.read_json(step.report_ref), old_step)
        lock = paths.execution_lock
        self.store.save(run.run_id, run, immutable=False)
        self.assertEqual(self.store.get(run.run_id, ExperimentRun), run)
        self.assertEqual(paths.legacy_state.read_bytes(), original)
        self.assertEqual(paths.execution_lock, lock)
        self.assertEqual(self.store.ids(), ["old_run"])

    def test_inspection_does_not_create_storage(self) -> None:
        missing = self.root / "not-created"
        store = FileRunStore(missing)
        self.assertEqual(store.list(ExperimentRun), [])
        self.assertIsNone(store.get("run_a", ExperimentRun))
        self.assertFalse(missing.exists())


class ProxyFixture:
    def __init__(self, base_url: str, api_key: str):
        self.url = base_url
        self.requests: list[dict[str, str]] = []

    def close(self) -> None:
        pass


class ProbeWorkflow:
    name = "probe"

    def __init__(
        self, root: Path, records: FileRunStore, run: ExperimentRun, emit: Callable[[str], None]
    ):
        self.state = run

    async def run(self, context: WorkflowContext, plan: ExperimentPlan) -> ExperimentRun:
        previous = await context.memory.search("sentinel")
        if previous:
            raise AssertionError("Memory leaked from another Run")
        await context.memory.add("sentinel " + self.state.run_id, source=self.state.run_id)
        self.state.state = RunState.SUCCEEDED
        return self.state


class RunBindingTest(unittest.IsolatedAsyncioTestCase):
    async def test_app_binds_memory_workspaces_and_audits_to_each_run(self) -> None:
        env = {
            "RSI_TEACHER_MODEL": "fixture",
            "RSI_TEACHER_BASE_URL": "http://127.0.0.1:1/v1",
            "RSI_TARGET_MODEL": "fixture",
            "RSI_TARGET_BASE_URL": "http://127.0.0.1:1/v1",
        }
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict("os.environ", env, clear=True),
            patch("areal_rsi.app.GatewayProxy", ProxyFixture),
        ):
            root = Path(temporary)
            await FileMemory(root / "memory").add("sentinel legacy", source="legacy")
            app = await LocalApp.create(
                root, lambda _: None, workflows={"probe": ProbeWorkflow}, workflow_name="probe"
            )
            try:
                self.assertFalse((root / "conversations").exists())
                run_ids = []
                for _ in range(2):
                    started = await app.service.start(plan())
                    run = await app.service.tasks[started.run_id]
                    self.assertEqual(run.state, RunState.SUCCEEDED, run.error)
                    self.assertIsNone(run.plan.baseline_solution_id)
                    self.assertIsNone(run.best_solution_id)
                    run_ids.append(run.run_id)
                    paths = app.service.records.paths(run.run_id)
                    self.assertEqual(app.memory.records.root, paths.root / "memory")
                    for runtime in app.agent_runtimes.values():
                        self.assertEqual(runtime.root, paths.root / "agents")
                        self.assertTrue(runtime.agent_directories)
                        self.assertEqual(
                            runtime.trajectories.records.root, paths.root / "trajectories"
                        )
                    for researcher in app.service.context.research_agents:
                        assert isinstance(researcher, DefaultResearchAgent)
                        self.assertEqual(
                            researcher.root, paths.agent(researcher.agent_id) / "workspace"
                        )
                    for proxy in dict.fromkeys(app.agent_proxies.values()):
                        proxy.requests.append({"owner": run.run_id})
            finally:
                await app.close()
            for run_id in run_ids:
                paths = app.service.records.paths(run_id)
                memories = await FileMemory(paths.root / "memory").search("sentinel")
                self.assertEqual([hit.memory.source for hit in memories], [run_id])
                audits = list((paths.runtime / "gateway").glob("*.json"))
                self.assertEqual(len(audits), 1)
                self.assertEqual(json.loads(audits[0].read_text()), [{"owner": run_id}])
            self.assertFalse((root / "gateway").exists())
            self.assertFalse((root / "sessions").exists())
