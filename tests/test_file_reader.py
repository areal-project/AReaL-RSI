"""Bounded evidence responses and actual stdio tool registration, without a model."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from areal_rsi.modules import file_reader


class FileReaderTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.text = "A\x00\n" * 10000 + "END"
        (self.root / "output").write_text(self.text)
        (self.root / "trajectory.json").write_text(
            json.dumps(
                {
                    "events": [{"type": "tool/result", "content": self.text}],
                    "turns": ["question"],
                }
            )
        )
        (self.root / "private").write_text("PRIVATE")
        self.reader = file_reader.FileReader(self.root, ["output", "trajectory.json"])

    async def test_long_file_can_be_reconstructed_without_truncation(self) -> None:
        offset, parts = 0, []
        while True:
            raw = self.reader.read_file("output", offset, 4000)
            self.assertLessEqual(len(raw.encode()), file_reader.MAX_RESPONSE_BYTES)
            page = json.loads(raw)
            parts.append(page["text"])
            if page["next_offset"] is None:
                break
            self.assertGreater(page["next_offset"], offset)
            offset = page["next_offset"]
        self.assertEqual("".join(parts), self.text)
        self.assertNotIn("private", self.reader.list_files())

    async def test_json_selection_search_and_access_boundaries(self) -> None:
        result = json.loads(self.reader.read_json("trajectory.json", "/turns/0"))
        self.assertEqual(json.loads(result["text"]), "question")
        result = json.loads(self.reader.inspect_json("trajectory.json", "/events"))
        self.assertEqual(result, {"type": "array", "length": 1})
        result = json.loads(self.reader.search_file("output", "END"))
        self.assertEqual(result["match"], len(self.text) - 3)
        for name in ("private", "../private", str(self.root / "private")):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.reader.read_file(name)
        (self.root / "link").symlink_to(self.root / "private")
        with self.assertRaises(ValueError):
            file_reader.FileReader(self.root, ["link"])

    async def test_stdio_exposes_only_read_only_tools(self) -> None:
        grant = self.root / "grant.json"
        grant.write_text(json.dumps({"root": str(self.root), "paths": ["output"]}))
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[str(Path(file_reader.__file__)), str(grant)],
        )
        async with (
            stdio_client(parameters) as (read, write),
            ClientSession(read, write) as client,
        ):
            await client.initialize()
            tools = await client.list_tools()
            self.assertEqual(
                {tool.name for tool in tools.tools},
                {"list_files", "read_file", "read_json", "inspect_json", "search_file"},
            )
            result = await client.call_tool("read_file", {"path": "output", "limit": 200})
            self.assertFalse(getattr(result, "is_error", getattr(result, "isError", False)))
            content = result.content[0]
            assert content.type == "text"
            self.assertLessEqual(len(content.text.encode()), file_reader.MAX_RESPONSE_BYTES)
            denied = await client.call_tool("read_file", {"path": "private"})
            self.assertTrue(getattr(denied, "is_error", getattr(denied, "isError", False)))
