"""Experience stays in one semantic file per topic and rejects concurrent stale edits."""

import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fake_codex import FakeCodex, FakeThread

from areal_rsi.api.control import LessonDraft, encode_record
from areal_rsi.api.research import LearningRecord, SessionConfig, SessionRecord
from areal_rsi.infra.experience import MAX_BODY_CHARS, ExperienceLibrary
from areal_rsi.infra.research import ResearchSystem


class ExperienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_updates_replace_one_document_and_reject_stale_edits(self) -> None:
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
            )
            earlier = await system.create(source.config)
            path = Path(first.path)
            before = path.read_text()
            second = library.publish(
                LessonDraft(
                    "graph-testing",
                    "Graph checks",
                    "When updating graph state",
                    "Validate the complete batch; also check rollback with an oracle.",
                ),
                previous_content=before,
            )
            self.assertEqual(first.path, second.path)
            self.assertEqual(path, library.root / "graph-testing.md")
            self.assertFalse(path.is_symlink())
            self.assertEqual(list(library.root.iterdir()), [path])
            newer = await system.create(source.config)
            self.assertEqual(system.detail(newer).experience, [second])
            saved_path = system.detail(earlier).experience[0].path
            self.assertIn("also check rollback", Path(saved_path).read_text())
            with self.assertRaisesRegex(ValueError, "changed"):
                library.publish(
                    LessonDraft("graph-testing", "Title", "Description", "Stale edit"),
                    previous_content=before,
                )
            self.assertEqual(library.documents(), [second])
            self.assertIn("also check rollback", path.read_text())
            disabled = await system.create(SessionConfig(str(root), use_experience=False))
            self.assertFalse(system.detail(disabled).experience)

    async def test_unchanged_summary_does_not_rewrite_the_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = ExperienceLibrary(root / "experience")
            draft = LessonDraft("validation", "Validation", "After edits", "Check rendered output.")
            first = library.publish(draft)
            current = Path(first.path)
            before = current.read_bytes(), current.stat().st_mtime_ns
            repeated = library.publish(draft, previous_content=current.read_text())
            self.assertEqual(repeated, first)
            self.assertEqual((current.read_bytes(), current.stat().st_mtime_ns), before)
            self.assertEqual(list(library.root.iterdir()), [current])

    async def test_oversized_summary_keeps_existing_document_intact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = ExperienceLibrary(root / "experience")
            draft = LessonDraft("validation", "Validation", "After edits", "Useful compact advice.")
            original = library.publish(draft)
            before = Path(original.path).read_text()
            with self.assertRaisesRegex(ValueError, "too long.*condense"):
                library.publish(
                    replace(draft, body="冗" * (MAX_BODY_CHARS + 1)),
                    previous_content=before,
                )
            self.assertEqual(library.documents(), [original])
            self.assertEqual(Path(original.path).read_text(), before)
            self.assertEqual(list(library.root.iterdir()), [Path(original.path)])

    async def test_failed_replacement_keeps_original_and_cleans_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = ExperienceLibrary(root / "experience")
            draft = LessonDraft("validation", "Validation", "After edits", "Check visible output.")
            document = library.publish(draft)
            path = Path(document.path)
            before = path.read_text()
            with (
                patch("areal_rsi.infra.experience.os.replace", side_effect=OSError("write failed")),
                self.assertRaisesRegex(OSError, "write failed"),
            ):
                library.publish(
                    replace(draft, body="Check dimensions too."), previous_content=before
                )
            self.assertEqual(path.read_text(), before)
            self.assertEqual(list(library.root.iterdir()), [path])
            self.assertFalse(list(root.glob(".experience-*")))

    async def test_curation_merges_latest_document_and_can_choose_an_independent_topic(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = ExperienceLibrary(root / "experience")
            source = SessionRecord("source", SessionConfig(str(root)), thread_id="source-thread")
            draft = LessonDraft("validation", "Validation", "After edits", "OLD_ADVICE")
            first = library.publish(draft)
            source.learning = LearningRecord("completed", document=first)
            latest_draft = replace(draft, body="Check visible output; preserve rollback coverage.")
            library.publish(
                latest_draft,
                previous_content=Path(first.path).read_text(),
            )
            FakeCodex.instances.clear()
            FakeCodex.threads.clear()
            FakeCodex.threads[source.thread_id] = FakeThread()

            async def summarize(result: LessonDraft) -> FakeCodex:
                count = len(FakeCodex.instances)
                with patch("areal_rsi.infra.experience.CodexClient", FakeCodex):
                    task = asyncio.create_task(library.consolidate(source, root, "Check sizing"))
                    try:
                        async with asyncio.timeout(5):
                            while len(FakeCodex.instances) == count or not (
                                FakeCodex.instances[-1].turn_id
                            ):
                                await asyncio.sleep(0.01)
                        client = FakeCodex.instances[-1]
                        await client.finish(json.dumps(encode_record(result)))
                        document = await task
                    finally:
                        if not task.done():
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
                source.learning = LearningRecord("completed", document=document)
                self.assertTrue(client.closed)
                return client

            merged = replace(latest_draft, body=latest_draft.body + " Check element dimensions.")
            client = await summarize(merged)
            request = next(params for method, params in client.requests if method == "turn/start")
            context = request["additionalContext"]["rsi-experience"]["value"]
            self.assertIn(latest_draft.body, context)
            self.assertNotIn("OLD_ADVICE", context)
            self.assertEqual(len(library.documents()), 1)
            updated = library.documents()[0]
            self.assertIn(merged.body, Path(updated.path).read_text())

            await summarize(merged)
            self.assertEqual(library.documents(), [updated])
            await summarize(
                LessonDraft(
                    "db-migrations", "Database migrations", "Schema edits", "Check rollback."
                )
            )
            self.assertEqual(
                {doc.slug for doc in library.documents()}, {"validation", "db-migrations"}
            )
            self.assertIn(updated, library.documents())
            self.assertEqual(
                {path.name for path in library.root.iterdir()},
                {"validation.md", "db-migrations.md"},
            )

    async def test_filenames_reject_paths_hashes_and_numbering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = ExperienceLibrary(root / "experience")
            for slug in ("../escape", "experience-001", "2026-10-16", "abc123", "a" * 40):
                with self.subTest(slug=slug), self.assertRaisesRegex(ValueError, "slug"):
                    library.publish(LessonDraft(slug, "Title", "Description", "Text"))
            self.assertFalse(library.root.exists())
            self.assertFalse((root / "escape").exists())
