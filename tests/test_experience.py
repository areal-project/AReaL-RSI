"""Experience is versioned, read by fresh conversations, and never overwrites a newer edit."""

import tempfile
import unittest
from pathlib import Path

from areal_rsi.api.control import LessonDraft
from areal_rsi.api.research import SessionConfig, SessionRecord
from areal_rsi.infra.experience import ExperienceLibrary
from areal_rsi.infra.research import ResearchSystem


class ExperienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_revision_snapshot_and_concurrent_edit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            system = ResearchSystem(root / "data")
            library = system.experience
            source = SessionRecord("source", SessionConfig(str(root)))
            first = library.publish(
                LessonDraft(
                    "graph-testing",
                    "Graph checks",
                    "When updating graph state",
                    "Validate a batch before committing it.",
                ),
                source,
                "curator-1",
            )
            earlier = await system.create(source.config)
            self.assertEqual(Path(first.path).name, "graph-testing.md")
            second = library.publish(
                LessonDraft(
                    "graph-testing",
                    "Graph checks",
                    "When updating graph state",
                    "Validate the complete batch; also check rollback with an oracle.",
                ),
                source,
                "curator-2",
                previous_revision=first.revision,
            )
            self.assertEqual(system.detail(earlier).experience, [first])
            self.assertNotEqual(first.path, second.path)
            newer = await system.create(source.config)
            self.assertEqual(system.detail(newer).experience, [second])
            self.assertEqual(
                Path(first.path).read_text().splitlines()[-1],
                "Validate a batch before committing it.",
            )
            with self.assertRaisesRegex(ValueError, "changed"):
                library.publish(
                    LessonDraft("graph-testing", "Title", "Description", "Stale edit"),
                    source,
                    "curator-3",
                    previous_revision=first.revision,
                )
            self.assertEqual(library.snapshot(), [second])
            disabled = await system.create(SessionConfig(str(root), use_experience=False))
            self.assertFalse(system.detail(disabled).experience)

    async def test_agent_cannot_escape_library(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = ExperienceLibrary(root / "experience")
            with self.assertRaisesRegex(ValueError, "slug"):
                library.publish(
                    LessonDraft("../escape", "Title", "Description", "Text"),
                    SessionRecord("source", SessionConfig(str(root))),
                    "curator",
                )
            self.assertFalse((root / "escape").exists())
