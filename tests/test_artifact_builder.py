from __future__ import annotations

import difflib
import tempfile
import unittest
from pathlib import Path

from areal_rsi.api.base import ArtifactChange
from areal_rsi.base.storage import FileContentStore, FileVersionManager
from areal_rsi.modules.artifact import LocalArtifactBuilder


class ArtifactBuilderTest(unittest.IsolatedAsyncioTestCase):
    async def test_artifact_can_be_created_and_iterated_without_overwriting_base(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = FileContentStore(root / "content")
            versions = FileVersionManager(root / "versions")
            builder = LocalArtifactBuilder(store, root / "builds")
            first = "<html><body>Game prototype</body></html>\n"
            patch = "".join(
                difflib.unified_diff(
                    [], first.splitlines(True), fromfile="/dev/null", tofile="b/index.html"
                )
            )
            content = await builder.build(
                None, ArtifactChange("game", await store.put_text(patch), "创建原型")
            )
            base = await versions.create_artifact("game", content)
            second = "<html><body>Game prototype<button>Start</button></body></html>\n"
            patch = "".join(
                difflib.unified_diff(
                    first.splitlines(True),
                    second.splitlines(True),
                    fromfile="a/index.html",
                    tofile="b/index.html",
                )
            )
            changed = await builder.build(
                base, ArtifactChange("game", await store.put_text(patch), "增加开始按钮")
            )
            version = await versions.create_artifact(
                "game", changed, parent_version_id=base.version_id
            )
            self.assertEqual(version.parent_version_id, base.version_id)
            self.assertNotEqual(version.content.digest, base.content.digest)
            old = await store.materialize(base.content, root / "old")
            new = await store.materialize(version.content, root / "new")
            self.assertEqual((old / "index.html").read_text(), first)
            self.assertEqual((new / "index.html").read_text(), second)
            with self.assertRaises(ValueError):
                await builder.build(
                    base, ArtifactChange("other", await store.put_text(patch), "错误目标")
                )
            escape = "".join(
                difflib.unified_diff([], ["escape\n"], fromfile="/dev/null", tofile="b/../outside")
            )
            with self.assertRaises(ValueError):
                await builder.build(
                    base, ArtifactChange("game", await store.put_text(escape), "非法路径")
                )
            self.assertFalse((root / "outside").exists())
