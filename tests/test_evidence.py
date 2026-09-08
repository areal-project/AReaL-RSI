"""Generic file grants, content integrity and runtime access isolation."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from areal_rsi.api.base import ContentRef, EvidenceBundle, EvidenceFile
from areal_rsi.base.evidence import FileEvidenceStore
from areal_rsi.base.storage import FileContentStore
from areal_rsi.modules.runtime import sandbox_wrapper


class EvidenceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.content = FileContentStore(self.root / "content")
        self.store = FileEvidenceStore(self.content)
        self.public = await self.content.put_bytes(b"\x00\x01public", media_type="image/example")
        self.hidden = await self.content.put_text("PRIVATE_VALIDATION")
        self.bundle = await self.store.put(
            EvidenceBundle(
                (
                    EvidenceFile("case/output.bin", self.public, "custom-output", {"task": "case"}),
                    EvidenceFile("hidden.txt", self.hidden, "private"),
                ),
                {"private-summary": "must not leak into exported index"},
            )
        )

    async def test_exact_grant_binary_content_and_immutable_originals(self) -> None:
        destination = self.root / "view"
        grant = await self.store.materialize(self.bundle, destination, paths=["case/output.bin"])
        selected = await self.store.get(grant)
        self.assertEqual([entry.path for entry in selected.files], ["case/output.bin"])
        self.assertEqual(selected.metadata, {})
        index = json.loads((destination / "index.json").read_text())
        self.assertEqual(index["files"][0]["bytes"], len(b"\x00\x01public"))
        self.assertNotIn("hidden", json.dumps(index))
        self.assertNotIn("content://", json.dumps(index))
        output = destination / "case/output.bin"
        self.assertEqual(output.read_bytes(), b"\x00\x01public")
        output.write_bytes(b"changed copy")
        self.assertEqual(await self.content.read_bytes(self.public), b"\x00\x01public")
        with self.assertRaises(FileExistsError):
            await self.store.materialize(self.bundle, destination, paths=[])

    async def test_paths_collisions_and_unknown_grants_rejected(self) -> None:
        for names in (
            ["../escape"],
            ["/absolute"],
            ["a\\b"],
            ["a/../b"],
            ["./a"],
            ["a//b"],
            [""],
            ["index.json"],
            ["INDEX.JSON/x"],
            ["a\nfile"],
            ["a", "a"],
            ["a", "A"],
            ["a", "a/b"],
        ):
            with self.subTest(names=names), self.assertRaises(ValueError):
                await self.store.put(
                    EvidenceBundle(tuple(EvidenceFile(name, self.public, "file") for name in names))
                )
        for paths in (["missing"], ["hidden.txt", "hidden.txt"]):
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                await self.store.materialize(self.bundle, self.root / "not-created", paths=paths)
            self.assertFalse((self.root / "not-created").exists())

    async def test_symlink_destinations_and_corrupt_content_rejected(self) -> None:
        (self.root / "link").symlink_to(self.root, target_is_directory=True)
        for destination in (self.root / "link", self.root / "link/view"):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                await self.store.materialize(self.bundle, destination, paths=["case/output.bin"])
        bad = ContentRef(self.public.uri, "sha256:" + "0" * 64, self.public.media_type)
        bundle = await self.store.put(EvidenceBundle((EvidenceFile("bad", bad, "output"),)))
        with self.assertRaises(ValueError):
            await self.store.materialize(bundle, self.root / "corrupt", paths=["bad"])
        self.assertFalse((self.root / "corrupt/index.json").exists())

    @unittest.skipUnless(sys.platform == "darwin", "macOS sandbox-exec integration")
    async def test_runtime_cannot_read_ungranted_backing_content_or_sibling_view(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js unavailable")
        workspace = self.root / "workspace"
        workspace.mkdir()
        await self.store.materialize(self.bundle, workspace / "evidence", paths=["case/output.bin"])
        sibling = self.root / "other-agent"
        await self.store.materialize(self.bundle, sibling, paths=["hidden.txt"])
        session = self.root / "session"
        session.mkdir()
        cli = self.root / "installation/node_modules/.bin/dsh"
        cli.parent.mkdir(parents=True)
        sandbox_wrapper(session, workspace, cli)
        command = ["/usr/bin/sandbox-exec", "-f", str(session / "sandbox.sb"), "/bin/cat"]
        public = subprocess.run(
            [*command, str(workspace / "evidence/case/output.bin")], capture_output=True
        )
        self.assertEqual(public.stdout, b"\x00\x01public", public.stderr)
        hidden = await self.content.resolve_path(self.hidden)
        (workspace / "escape").symlink_to(hidden)
        for path in (hidden, sibling / "hidden.txt", workspace / "escape"):
            with self.subTest(path=path):
                denied = subprocess.run([*command, str(path)], capture_output=True)
                self.assertNotEqual(denied.returncode, 0)
                self.assertNotIn(b"PRIVATE_VALIDATION", denied.stdout)
