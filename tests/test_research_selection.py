import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from areal_rsi.agents.research import DefaultResearchAgent
from areal_rsi.api.base import BenchmarkSplit, EvaluationReport, TrajectoryRef
from areal_rsi.base.storage import FileContentStore, FileMemory, FileVersionManager, now
from areal_rsi.modules.runtime import DshAgentRuntime, DshSession


class ResearchSelectionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        content = FileContentStore(root / "content")
        versions = FileVersionManager(root / "versions")
        ref = await content.put_text("fixture")
        model = await versions.create_model("m", ref)
        harness = await versions.create_harness("h", ref)
        first = await versions.create_solution(model, harness)
        second = await versions.create_solution(
            model, harness, parent_solution_id=first.solution_id
        )
        self.reports = [
            EvaluationReport(first.solution_id, BenchmarkSplit.VALIDATE, True, 1.0, {}, now()),
            EvaluationReport(second.solution_id, BenchmarkSplit.VALIDATE, False, 0.5, {}, now()),
        ]
        self.session = Mock(spec=DshSession)
        self.session.send = AsyncMock()
        self.session.finish = AsyncMock(return_value=TrajectoryRef("selection-trace"))
        runtime = Mock(spec=DshAgentRuntime)
        runtime.content = content
        runtime.open = AsyncMock(return_value=self.session)
        self.agent = DefaultResearchAgent(
            "research",
            runtime,
            model,
            content,
            FileMemory(root / "memory"),
            versions,
            root / "workspaces",
        )

    async def test_maps_model_choice_without_replacing_it_with_highest_score(self) -> None:
        self.session.send.return_value = '{"candidate_index": 1}'
        selected = await self.agent.select(self.reports[0].solution_id, self.reports)
        self.assertEqual(selected, self.reports[1].solution_id)
        self.assertEqual(self.reports[1].score, 0.5)
        prompt = json.loads(self.session.send.await_args.args[0])
        self.assertEqual([x["candidate_index"] for x in prompt["candidates"]], [0, 1])
        self.session.finish.assert_awaited_once()

    async def test_invalid_reply_gets_one_bounded_correction(self) -> None:
        self.session.send.side_effect = ['{"next_solution_id":"typo"}', '{"candidate_index":0}']
        selected = await self.agent.select(self.reports[0].solution_id, self.reports)
        self.assertEqual(selected, self.reports[0].solution_id)
        self.assertEqual(self.session.send.await_count, 2)

    async def test_missing_boolean_and_out_of_range_indices_do_not_silently_stop(self) -> None:
        for value in (
            {},
            {"candidate_index": True},
            {"candidate_index": -1},
            {"candidate_index": 2},
        ):
            self.session.send.reset_mock()
            self.session.send.return_value = json.dumps(value)
            with self.assertRaises(ValueError):
                await self.agent.select(self.reports[0].solution_id, self.reports)
            self.assertEqual(self.session.send.await_count, 2)

    async def test_explicit_null_stops_the_line(self) -> None:
        self.session.send.return_value = '{"candidate_index": null}'
        self.assertIsNone(await self.agent.select(self.reports[0].solution_id, self.reports))
