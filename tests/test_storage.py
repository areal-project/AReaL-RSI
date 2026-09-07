import tempfile
import unittest
from pathlib import Path

from areal_rsi.base.storage import FileContentStore, FileMemory, FileVersionManager


class StorageTest(unittest.IsolatedAsyncioTestCase):
    async def test_component_types_and_artifact_registration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            content = FileContentStore(root / "content")
            versions = FileVersionManager(root / "versions")
            ref = await content.put_text("snapshot")
            model = await versions.create_model("model", ref)
            harness = await versions.create_harness("harness", ref)
            self.assertIsNone(await versions.get_model(harness.version_id))
            with self.assertRaises(ValueError):
                await versions.create_harness("bad-parent", ref, parent_version_id=model.version_id)
            artifact = await versions.create_artifact("game", ref)
            await versions.create_solution(model, harness, artifacts=(artifact,))
            artifact.name = "changed"
            with self.assertRaises(ValueError):
                await versions.create_solution(model, harness, artifacts=(artifact,))

    async def test_memory_preserves_text_and_finds_chinese_experience(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            memory = FileMemory(Path(directory))
            text = "工具验证失败时检查参数和输出格式。" * 1000
            record = await memory.add(text, source="trajectory:example", tags=("验证",))
            stored = await memory.get(record.memory_id)
            assert stored is not None
            self.assertEqual(stored.text, text)
            hits = await memory.search("如何改善工具验证？")
            self.assertEqual(hits[0].memory.memory_id, record.memory_id)
            self.assertEqual(await memory.search("不存在的主题xyz"), [])

    async def test_reload_is_detached_and_content_is_verified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            content = FileContentStore(root / "content")
            versions = FileVersionManager(root / "versions")
            ref = await content.put_text("snapshot")
            model = await versions.create_model("m", ref)
            harness = await versions.create_harness("h", ref)
            solution = await versions.create_solution(model, harness)
            model.name = "changed in caller"
            loaded = await versions.get_solution(solution.solution_id)
            assert loaded is not None
            self.assertEqual(loaded.model.name, "m")
            path = await content.resolve_path(ref)
            path.write_text("corrupted")
            with self.assertRaises(ValueError):
                await content.read_text(ref)

    async def test_snapshot_rejects_symlinks_and_materializes_same_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = FileContentStore(root / "content")
            source = root / "harness"
            source.mkdir()
            (source / "prompt.md").write_text("generic reasoning")
            ref = await store.snapshot(source)
            restored = await store.materialize(ref, root / "restored")
            self.assertEqual((restored / "prompt.md").read_text(), "generic reasoning")
            (source / "escape").symlink_to(root)
            with self.assertRaises(ValueError):
                await store.snapshot(source)
