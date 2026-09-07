"""本地 Task 输入与临时 MCP 生命周期的离线契约测试。"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from areal_rsi.api.base import ContentRef, JsonObject, Task, TrajectoryRef
from areal_rsi.base.storage import FileContentStore, FileVersionManager
from areal_rsi.modules.harness import LocalHarnessRuntime
from areal_rsi.modules.runtime import DshAgentRuntime, DshSession


class HarnessInputTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.content = FileContentStore(self.root / "content")
        versions = self.versions = FileVersionManager(self.root / "versions")
        source = self.root / "harness"
        (source / "tools").mkdir(parents=True)
        (source / "prompt.md").write_text("Read the public task and answer.\n")
        (source / "tools" / "helper.py").write_text("print('helper')\n")
        (source / "mcp.json").write_text(
            json.dumps({"servers": [{"name": "helper", "script": "tools/helper.py"}]})
        )
        self.solution = await versions.create_solution(
            await versions.create_model("fixture", await self.content.put_text("model")),
            await versions.create_harness("fixture", await self.content.snapshot(source)),
        )
        self.session = Mock(spec=DshSession)
        self.session.send = AsyncMock(return_value="fixture response")
        self.session.finish = AsyncMock(return_value=TrajectoryRef("fixture-trace"))
        self.runtime = Mock(spec=DshAgentRuntime)
        self.runtime.mcp_registry = {"existing": {"serverName": "keep"}}
        self.runtime.open = AsyncMock(return_value=self.session)
        self.harness = LocalHarnessRuntime(self.runtime, self.content, self.root / "workspaces")

    async def test_json_input_remains_unchanged_and_mcp_is_released(self) -> None:
        value: JsonObject = {"question": "公开任务"}
        result = await self.harness.run(self.solution, Task("task", value))
        self.session.send.assert_awaited_once_with(json.dumps(value, ensure_ascii=False))
        self.session.finish.assert_awaited_once()
        self.assertEqual(result.trajectory.trajectory_id, "fixture-trace")
        self.assertEqual(self.runtime.mcp_registry, {"existing": {"serverName": "keep"}})

    async def test_file_ref_is_materialized_without_copying_content_into_prompt(self) -> None:
        text = "long public input\n" * 1000
        ref = await self.content.put_text(text)
        await self.harness.run(self.solution, Task("task", ref))
        message = self.session.send.await_args.args[0]
        self.assertLess(len(message), 500)
        self.assertNotIn(ref.uri, message)
        data = json.loads(message)
        workspace = Path(self.runtime.open.await_args.kwargs["workspace_root"])
        self.assertEqual((workspace / data["input_path"]).read_text(), text)
        self.assertEqual(data["input_kind"], "file")
        self.assertEqual(await self.content.read_text(ref), text)

    async def test_directory_ref_keeps_nested_public_files(self) -> None:
        source = self.root / "public"
        (source / "nested").mkdir(parents=True)
        (source / "nested" / "question.txt").write_text("question")
        ref = await self.content.snapshot(source)
        await self.harness.run(self.solution, Task("task", ref))
        data = json.loads(self.session.send.await_args.args[0])
        workspace = Path(self.runtime.open.await_args.kwargs["workspace_root"])
        self.assertEqual(data["input_kind"], "directory")
        self.assertEqual(
            (workspace / data["input_path"] / "nested" / "question.txt").read_text(), "question"
        )

    async def test_solution_artifact_input_is_copied_without_mutating_its_version(self) -> None:
        source = self.root / "game"
        source.mkdir()
        (source / "index.html").write_text("original game")
        artifact = await self.versions.create_artifact("游戏", await self.content.snapshot(source))
        solution = await self.versions.create_solution(
            self.solution.model,
            self.solution.harness,
            artifacts=(artifact,),
        )
        await self.harness.run(solution, Task("task", "inspect game"))
        message = json.loads(self.session.send.await_args.args[0])
        self.assertEqual(message["task_input"], "inspect game")
        item = message["artifact_inputs"][0]
        self.assertEqual(item["name"], "游戏")
        self.assertEqual(item["version_id"], artifact.version_id)
        workspace = Path(self.runtime.open.await_args.kwargs["workspace_root"])
        copied = workspace / item["path"] / "index.html"
        self.assertEqual(copied.read_text(), "original game")
        copied.write_text("working copy changed")
        restored = await self.content.materialize(artifact.content, self.root / "restored")
        self.assertEqual((restored / "index.html").read_text(), "original game")

    async def test_bad_digest_is_rejected_before_opening_session(self) -> None:
        ref = await self.content.put_text("task")
        path = await self.content.resolve_path(ref)
        path.write_text("tampered")
        with self.assertRaises(ValueError):
            await self.harness.run(self.solution, Task("task", ContentRef(ref.uri, ref.digest)))
        self.runtime.open.assert_not_awaited()
        self.assertEqual(self.runtime.mcp_registry, {"existing": {"serverName": "keep"}})

    async def test_initialization_failure_cleans_only_own_mcp_entries(self) -> None:
        self.runtime.open.side_effect = RuntimeError("initialization failed")
        with self.assertRaisesRegex(RuntimeError, "initialization failed"):
            await self.harness.run(self.solution, Task("task", "public task"))
        self.session.finish.assert_not_awaited()
        self.assertEqual(self.runtime.mcp_registry, {"existing": {"serverName": "keep"}})

    async def test_cancellation_closes_session_and_cleans_registry(self) -> None:
        self.session.send.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.harness.run(self.solution, Task("task", "public task"))
        self.session.finish.assert_awaited_once()
        self.assertEqual(self.runtime.mcp_registry, {"existing": {"serverName": "keep"}})

    async def test_registry_is_cleaned_even_when_finish_fails(self) -> None:
        self.session.finish.side_effect = RuntimeError("finish failed")
        with self.assertRaisesRegex(RuntimeError, "finish failed"):
            await self.harness.run(self.solution, Task("task", "public task"))
        self.assertEqual(self.runtime.mcp_registry, {"existing": {"serverName": "keep"}})
