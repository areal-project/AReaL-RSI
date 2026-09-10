"""Research reads separate, complete evidence files instead of injected feedback."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from areal_rsi.agents.research import DefaultResearchAgent
from areal_rsi.api.base import (
    BenchmarkSplit,
    EvaluationReport,
    EvidenceBundle,
    EvidenceFile,
    EvolutionTarget,
    TrajectoryRef,
)
from areal_rsi.base.evidence import FileEvidenceStore
from areal_rsi.base.storage import FileContentStore, FileMemory, FileVersionManager, encode, now
from areal_rsi.modules.runtime import DshAgentRuntime, DshSession


class ResearchFeedbackTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        self.content = FileContentStore(root / "content")
        self.store = FileEvidenceStore(self.content)
        versions = FileVersionManager(root / "versions")
        harness_dir = root / "harness"
        harness_dir.mkdir()
        (harness_dir / "prompt.md").write_text("Original instructions.\n")
        model = await versions.create_model("fake", await self.content.put_json({}))
        harness = await versions.create_harness("h", await self.content.snapshot(harness_dir))
        self.solution = await versions.create_solution(model, harness)
        self.report = EvaluationReport(
            self.solution.solution_id, BenchmarkSplit.TRAIN, False, 0.0, {}, now()
        )
        self.raw = await self.content.put_json({"turns": []})
        self.error = "diagnostic " * 1000 + "FINAL_ERROR"
        self.bundle = await self.store.put(
            EvidenceBundle(
                (
                    EvidenceFile(
                        "report.json", await self.content.put_json(encode(self.report)), "report"
                    ),
                    EvidenceFile("trajectory.json", self.raw, "trajectory"),
                    EvidenceFile("output", await self.content.put_text("x" * 50000), "output"),
                    EvidenceFile(
                        "verification.json",
                        await self.content.put_json({"feedback": self.error}),
                        "verification",
                    ),
                    EvidenceFile("private.json", await self.content.put_text("PRIVATE"), "hidden"),
                ),
                {"solution_id": self.solution.solution_id, "split": "train"},
            )
        )
        self.session = Mock(spec=DshSession)
        self.session.send = AsyncMock(
            return_value=json.dumps(
                {
                    "reason": "Preserve the interface",
                    "files": {"prompt.md": "Inspect the contract.\n"},
                }
            )
        )
        self.session.finish = AsyncMock(return_value=TrajectoryRef("research-trace"))
        self.runtime = Mock(spec=DshAgentRuntime)
        self.runtime.content = self.content
        self.runtime.open = AsyncMock(return_value=self.session)
        self.agent = DefaultResearchAgent(
            "research",
            self.runtime,
            model,
            self.content,
            FileMemory(root / "memory"),
            versions,
            root / "workspaces",
            evidence_access=lambda f: f.kind != "hidden",
        )

    async def test_paths_only_message_complete_separate_files_and_grant(self) -> None:
        await self.agent.propose(
            "goal", EvolutionTarget.HARNESS, self.solution, self.report, self.bundle
        )
        message = self.session.send.await_args.args[0]
        payload = json.loads(message)
        self.assertNotIn("verification_feedback", payload)
        self.assertNotIn("report", payload)
        self.assertNotIn("diagnostic", message)
        self.assertNotIn("x" * 100, message)
        workspace = Path(self.runtime.open.await_args.kwargs["workspace_root"])
        train = workspace / "train"
        self.assertEqual(
            json.loads((train / "verification.json").read_text())["feedback"], self.error
        )
        self.assertEqual((train / "output").read_text(), "x" * 50000)
        self.assertFalse((train / "private.json").exists())
        self.assertNotIn("private", (train / "index.json").read_text())
        self.assertEqual(await self.content.read_json(self.raw), {"turns": []})
        audit = json.loads((workspace.parent / f"{workspace.name}.evidence.json").read_text())
        self.assertIn("grant", audit)
        self.assertEqual(len(list(train.iterdir())), 5)

    async def test_wrong_solution_and_validate_bundles_rejected(self) -> None:
        bundle = await self.store.get(self.bundle)
        for metadata in (
            {"solution_id": "another", "split": "train"},
            {"solution_id": self.solution.solution_id, "split": "validate"},
        ):
            bundle.metadata = dict(metadata)
            with self.assertRaises(ValueError):
                await self.agent.propose(
                    "goal",
                    EvolutionTarget.HARNESS,
                    self.solution,
                    self.report,
                    await self.store.put(bundle),
                )
        self.report.split = BenchmarkSplit.VALIDATE
        with self.assertRaises(ValueError):
            await self.agent.propose(
                "goal", EvolutionTarget.HARNESS, self.solution, self.report, self.bundle
            )
        self.runtime.open.assert_not_awaited()
