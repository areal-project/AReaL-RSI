from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from areal_rsi.agents.research.artifact import ArtifactResearchAgent
from areal_rsi.api.base import (
    ArtifactChange,
    ArtifactVersion,
    BenchmarkSplit,
    EvaluationReport,
    EvolutionTarget,
    SolutionVersion,
    Trajectory,
    TrajectoryRef,
)
from areal_rsi.base.storage import (
    FileContentStore,
    FileMemory,
    FileTrajectoryStore,
    FileVersionManager,
    now,
)
from areal_rsi.modules.artifact import LocalArtifactBuilder
from areal_rsi.modules.runtime import DshAgentRuntime, DshSession


class ArtifactResearchTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.content = FileContentStore(self.root / "content")
        self.versions = FileVersionManager(self.root / "versions")
        self.memory = FileMemory(self.root / "memory")
        self.builder = LocalArtifactBuilder(self.content, self.root / "builds")
        self.model = await self.versions.create_model(
            "research-agent-fixture-model", await self.content.put_text("PRIVATE_MODEL_CONTENT")
        )
        harness_dir = self.root / "harness-source"
        harness_dir.mkdir()
        (harness_dir / "prompt.md").write_text("PRIVATE_HARNESS_CONTENT")
        self.harness = await self.versions.create_harness(
            "fixed-harness", await self.content.snapshot(harness_dir)
        )
        self.current = await self.versions.create_solution(self.model, self.harness)
        self.report = EvaluationReport(
            self.current.solution_id, BenchmarkSplit.TRAIN, False, 0.25, {}, now()
        )
        self.traces = FileTrajectoryStore(self.root / "trajectories", self.content)
        train = await self.traces.save(
            Trajectory(
                "train-trace",
                "worker",
                await self.content.put_json(
                    {"turns": [{"role": "user", "content": "PUBLIC_TRAIN_EVIDENCE"}]}
                ),
                {},
                now(),
            )
        )
        await self.traces.save(
            Trajectory(
                "validate-trace",
                "worker",
                await self.content.put_json({"hidden": "PRIVATE_VALIDATE_DETAILS"}),
                {},
                now(),
            )
        )
        self.bundle = await self.traces.bundle([train])
        self.session = Mock(spec=DshSession)
        self.session.send = AsyncMock()
        self.session.finish = AsyncMock(return_value=TrajectoryRef("proposal-trace"))
        self.runtime = Mock(spec=DshAgentRuntime)
        self.runtime.content = self.content
        self.runtime.open = AsyncMock(return_value=self.session)
        self.agent = ArtifactResearchAgent(
            "artifact-research",
            self.runtime,
            self.model,
            self.content,
            self.memory,
            self.versions,
            self.root / "workspaces",
            artifact_name="user-game",
        )

    async def artifact(self, name: str, text: str) -> ArtifactVersion:
        directory = self.root / f"{name}-source"
        directory.mkdir()
        (directory / "index.html").write_text(text)
        return await self.versions.create_artifact(name, await self.content.snapshot(directory))

    async def propose(self, current: SolutionVersion | None = None) -> ArtifactChange:
        current = current or self.current
        return await self.agent.propose(
            "Build a playable game",
            EvolutionTarget.ARTIFACT,
            current,
            replace(self.report, solution_id=current.solution_id),
            self.bundle,
        )

    def workspace(self) -> Path:
        return Path(self.runtime.open.await_args.kwargs["workspace_root"])

    async def test_create_first_artifact_from_empty_base(self) -> None:
        self.session.send.return_value = json.dumps(
            {"files": {"index.html": "<button>Start</button>"}, "reason": "Create playable entry"}
        )
        change = await self.propose()
        self.assertIsInstance(change, ArtifactChange)
        self.assertEqual(change.artifact_name, "user-game")
        self.assertEqual(list((self.workspace() / "artifact").iterdir()), [])
        self.assertTrue(json.loads(self.session.send.await_args.args[0])["creating"])
        content = await self.builder.build(None, change)
        artifact = await self.versions.create_artifact(change.artifact_name, content)
        candidate = await self.versions.create_solution(
            self.current.model,
            self.current.harness,
            artifacts=[artifact],
            parent_solution_id=self.current.solution_id,
        )
        materialized = await self.content.materialize(content, self.root / "result")
        self.assertEqual((materialized / "index.html").read_text(), "<button>Start</button>")
        self.assertEqual(candidate.model, self.current.model)
        self.assertEqual(candidate.harness, self.current.harness)
        self.assertEqual((await self.versions.get_solution(self.current.solution_id)), self.current)
        self.assertEqual(self.agent.last_trajectory, "proposal-trace")
        self.session.finish.assert_awaited_once()

    async def test_iterate_selected_artifact_using_stored_baseline(self) -> None:
        artifact = await self.artifact("user-game", "original without newline")
        other = await self.artifact("other-product", "PRIVATE_OTHER_ARTIFACT")
        current = await self.versions.create_solution(
            self.model, self.harness, artifacts=[artifact, other]
        )

        async def reply(prompt: str) -> str:
            workspace = self.workspace()
            self.assertEqual(
                (workspace / "artifact" / "index.html").read_text(), "original without newline"
            )
            (workspace / "artifact" / "index.html").write_text("tampered working baseline")
            (workspace / "artifact" / "unsubmitted.txt").write_text("not a submitted edit")
            return json.dumps(
                {"files": {"index.html": "improved without newline"}, "reason": "Improve controls"}
            )

        self.session.send.side_effect = reply
        change = await self.propose(current)
        patch = await self.content.read_text(change.patch)
        self.assertIn("-original without newline", patch)
        self.assertNotIn("tampered", patch)
        self.assertNotIn("unsubmitted", patch)
        content = await self.builder.build(artifact, change)
        updated = await self.versions.create_artifact(
            artifact.name, content, parent_version_id=artifact.version_id
        )
        candidate = await self.versions.create_solution(
            current.model,
            current.harness,
            artifacts=[updated, other],
            parent_solution_id=current.solution_id,
        )
        original = await self.content.materialize(artifact.content, self.root / "original")
        result = await self.content.materialize(updated.content, self.root / "result")
        self.assertEqual((original / "index.html").read_text(), "original without newline")
        self.assertEqual((result / "index.html").read_text(), "improved without newline")
        self.assertFalse((result / "unsubmitted.txt").exists())
        self.assertEqual(updated.parent_version_id, artifact.version_id)
        self.assertEqual(candidate.artifacts[1], other)
        self.assertEqual(candidate.model, current.model)
        self.assertEqual(candidate.harness, current.harness)

    async def test_new_empty_files_are_applied_alone_and_with_other_edits(self) -> None:
        base = await self.artifact("user-game", "original\n")
        current = await self.versions.create_solution(self.model, self.harness, artifacts=[base])
        for index, (baseline, solution, files) in enumerate(
            (
                (None, self.current, {"package/__init__.py": ""}),
                (base, current, {"package/__init__.py": "", "index.html": "improved\n"}),
                (base, current, {"index.html": "improved\n", "package/__init__.py": ""}),
            )
        ):
            with self.subTest(index=index):
                self.session.send.return_value = json.dumps(
                    {"files": files, "reason": "Add a package marker"}
                )
                change = await self.propose(solution)
                content = await self.builder.build(baseline, change)
                result = await self.content.materialize(
                    content, self.root / f"empty-result-{index}"
                )
                for name, expected in files.items():
                    self.assertTrue((result / name).is_file())
                    self.assertEqual((result / name).read_text(), expected)
        original = await self.content.materialize(base.content, self.root / "empty-original")
        self.assertEqual((original / "index.html").read_text(), "original\n")
        self.assertFalse((original / "package" / "__init__.py").exists())

    async def test_workspace_contains_train_and_bounded_scoped_memory_only(self) -> None:
        other = await self.artifact("other-product", "PRIVATE_OTHER_ARTIFACT")
        current = await self.versions.create_solution(self.model, self.harness, artifacts=[other])
        for index in range(9):
            await self.memory.add(
                f"Prior lesson {index}: " + "x" * 3000,
                source=f"trajectory:old-{index}",
                tags=("artifact", "train", "artifact:user-game"),
            )
        for tags in (
            ("artifact", "validate", "artifact:user-game"),
            ("artifact", "train", "validate", "artifact:user-game"),
            ("artifact", "train", "artifact:other-product"),
            (),
        ):
            await self.memory.add("PRIVATE_VALIDATE_DETAILS", source="trajectory:hidden", tags=tags)

        async def reply(prompt: str) -> str:
            workspace = self.workspace()
            self.assertEqual(
                {path.name for path in workspace.iterdir()}, {"artifact", "train", "memory.json"}
            )
            memories = json.loads((workspace / "memory.json").read_text())
            self.assertEqual(len(memories), 5)
            self.assertTrue(all(len(record["text"]) <= 2000 for record in memories))
            visible = prompt + "".join(
                path.read_text() for path in workspace.rglob("*") if path.is_file()
            )
            self.assertIn("PUBLIC_TRAIN_EVIDENCE", visible)
            self.assertNotIn("PRIVATE_", visible)
            self.assertNotIn(str(self.content.root), visible)
            self.assertFalse(any(path.is_symlink() for path in workspace.rglob("*")))
            return '{"files":{"index.html":"first version"},"reason":"Create game"}'

        self.session.send.side_effect = reply
        await self.propose(current)
        specification = self.runtime.open.await_args.args[0]
        self.assertEqual(specification.tools, ("bash@v1",))
        self.assertEqual(specification.model, self.model)
        self.assertEqual(specification.skills, ())
        self.assertEqual(specification.mcp_servers, ())

    async def test_rejects_wrong_target_solution_and_validate_report_before_session(self) -> None:
        for target, report in (
            (EvolutionTarget.MODEL, self.report),
            (EvolutionTarget.HARNESS, self.report),
            (EvolutionTarget.ARTIFACT, replace(self.report, solution_id="unrelated")),
            (EvolutionTarget.ARTIFACT, replace(self.report, split=BenchmarkSplit.VALIDATE)),
        ):
            with self.subTest(target=target, report=report), self.assertRaises(ValueError):
                await self.agent.propose("goal", target, self.current, report, self.bundle)
        self.runtime.open.assert_not_awaited()

    async def test_rejects_invalid_files_paths_and_output(self) -> None:
        proposals = [
            {"files": {}, "reason": "empty"},
            {"files": [], "reason": "invalid"},
            {"files": {"index.html": "text"}, "reason": ""},
            {"files": {"index.html": "text"}, "reason": 12},
            {"files": {"index.html": "text"}, "reason": "x", "artifact_name": "other"},
            {"files": {"index.html": None}, "reason": "invalid"},
            {"files": {"index.html": "x" * 100_001}, "reason": "too large"},
            {"files": {f"file-{index}": "x" for index in range(9)}, "reason": "too many"},
        ]
        for path in (
            "../escape",
            "/tmp/escape",
            "nested/../../escape",
            "a\\b",
            ".",
            "a//b",
            "./index.html",
            ".git/config",
            "name\n--- /dev/null",
            "name\x00",
        ):
            proposals.append({"files": {path: "escape"}, "reason": "unsafe path"})
        for proposal in proposals:
            with self.subTest(proposal=str(proposal)[:150]):
                self.session.send.return_value = json.dumps(proposal)
                with self.assertRaises(ValueError):
                    await self.propose()
        self.assertEqual(list(self.memory.records.root.glob("*.json")), [])
        self.assertFalse((self.root / "escape").exists())
        self.assertEqual(self.session.finish.await_count, len(proposals))

    async def test_direct_workspace_edits_are_diffed_against_immutable_baseline(self) -> None:
        artifact = await self.artifact("user-game", "original\n")
        current = await self.versions.create_solution(
            self.model, self.harness, artifacts=[artifact]
        )

        async def reply(prompt: str) -> str:
            target = self.workspace() / "artifact"
            (target / "index.html").write_text("generated from reusable method\n")
            (target / "unsubmitted.txt").write_text("not part of the candidate")
            return '{"workspace_files":["index.html"],"reason":"Apply a reusable method"}'

        self.session.send.side_effect = reply
        change = await self.propose(current)
        patch = await self.content.read_text(change.patch)
        self.assertIn("-original", patch)
        self.assertNotIn("unsubmitted", patch)
        built = await self.builder.build(artifact, change)
        result = await self.content.materialize(built, self.root / "direct-result")
        self.assertEqual((result / "index.html").read_text(), "generated from reusable method\n")
        self.assertFalse((result / "unsubmitted.txt").exists())

    async def test_direct_workspace_proposal_rejects_symlink_escape(self) -> None:
        private = self.root / "private.txt"
        private.write_text("private content")

        async def reply(prompt: str) -> str:
            (self.workspace() / "artifact/leak.txt").symlink_to(private)
            return '{"workspace_files":["leak.txt"],"reason":"unsafe"}'

        self.session.send.side_effect = reply
        with self.assertRaisesRegex(ValueError, "escapes"):
            await self.propose()

    async def test_rejects_unchanged_snapshot(self) -> None:
        artifact = await self.artifact("user-game", "unchanged\n")
        current = await self.versions.create_solution(
            self.model, self.harness, artifacts=[artifact]
        )
        self.session.send.return_value = json.dumps(
            {"files": {"index.html": "unchanged\n"}, "reason": "No actual change"}
        )
        with self.assertRaisesRegex(ValueError, "实际修改"):
            await self.propose(current)

    async def test_selection_preserves_agent_choice_of_lower_scoring_version(self) -> None:
        second = await self.versions.create_solution(
            self.model, self.harness, parent_solution_id=self.current.solution_id
        )
        candidates = [
            replace(self.report, split=BenchmarkSplit.VALIDATE, score=1.0),
            replace(
                self.report,
                solution_id=second.solution_id,
                split=BenchmarkSplit.VALIDATE,
                score=0.5,
            ),
        ]
        self.session.send.return_value = '{"candidate_index": 1}'
        self.assertEqual(
            await self.agent.select(self.current.solution_id, candidates), second.solution_id
        )
        self.assertNotIn("PRIVATE_VALIDATE_DETAILS", self.session.send.await_args.args[0])
        self.session.finish.assert_awaited_once()
