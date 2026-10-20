"""Plugin regressions: retries are safe and work outlives the host connection."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent
from native_model import Json, NativeModel, function

from areal_rsi.api.control import ServiceRequest
from areal_rsi.api.research import SessionStatus, UserPrompt
from areal_rsi.infra.service.handler import SessionService
from areal_rsi.infra.service.transport import call, socket_path


class PluginTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_and_restart_do_not_repeat_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = SessionService(root / "data")
            create = ServiceRequest(
                "rsi_create",
                {
                    "request_id": "create",
                    "workspace": directory,
                    "mode": "chat",
                    "message": "",
                },
            )

            async def pending(run_id: str, feedback: str) -> None:
                await asyncio.Event().wait()

            try:
                first, duplicate = await asyncio.gather(
                    service.handle(create), service.handle(create)
                )
                self.assertEqual(first["status"], "completed", first)
                self.assertEqual(first, duplicate)
                self.assertEqual(len(service.system.store.list_sessions()), 1)
                learn = ServiceRequest(
                    "rsi_learn",
                    {
                        "request_id": "learn",
                        "run_id": first["result"]["run_id"],
                    },
                )
                with patch.object(service.system, "learn", side_effect=pending) as curator:
                    self.assertEqual((await service.handle(learn))["status"], "running")
                    await service.close()
                    service = SessionService(root / "data")
                    store = service.system.store
                    abandoned = store.load(first["result"]["run_id"])
                    abandoned.status = SessionStatus.RUNNING
                    abandoned.active_turn_id = "unfinished-turn"
                    abandoned.pending_prompts = [
                        UserPrompt(1, "question", "Continue?", prompt_id="old-question")
                    ]
                    abandoned.learning.status = "running"
                    store.save(abandoned)
                    owned = replace(abandoned, run_id="owned-by-tui")
                    store.save(owned)
                    with store.acquire(owned.run_id):
                        service.recover()
                        self.assertEqual(store.load(owned.run_id), owned)
                    recovered = store.load(abandoned.run_id)
                    self.assertEqual(recovered.status, SessionStatus.PAUSED)
                    self.assertFalse(recovered.pending_prompts)
                    self.assertEqual(recovered.learning.status, "failed")
                    self.assertEqual(recovered.active_turn_id, "unfinished-turn")
                    self.assertEqual(await service.handle(create), first)
                    self.assertEqual((await service.handle(learn))["status"], "interrupted")
                    self.assertEqual(curator.await_count, 1)
                    self.assertFalse(service.system.live)
            finally:
                await service.close()

    @unittest.skipUnless(shutil.which("codex"), "Codex CLI required for offline native integration")
    async def test_goal_finishes_after_mcp_disconnect_with_host_context(self) -> None:
        blocked, release = threading.Event(), threading.Event()
        steps: list[str | list[Json]] = [
            function(
                "rsi_set_task",
                {
                    "objective": "Write a marker",
                    "instructions": "Once",
                    "acceptance": ["marker.txt contains done"],
                },
            ),
            function("rsi_start_goal", {}),
            "Handing off",
            function("exec_command", {"cmd": "printf done > marker.txt"}),
            function(
                "rsi_update_goal",
                {
                    "status": "completed",
                    "summary": "Marker written",
                    "evidence": ["marker.txt contains done"],
                },
            ),
            "Finished",
        ]

        def respond(request: Json) -> str | list[Json]:
            if len(steps) == 3:
                blocked.set()
                release.wait(20)
            return steps.pop(0) if steps else "Unexpected call"

        model = NativeModel(respond)
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                data = root / "data"
                (root / ".env").write_text("RSI_API_KEY=offline-fixture\n")
                (root / "rsi.toml").write_text(
                    f'[session]\nbase_url = "{model.url}"\nauto_learn = false\n'
                )
                parameters = StdioServerParameters(
                    command=sys.executable,
                    args=["-m", "areal_rsi", "mcp", "--data-root", str(data)],
                    env={k: v for k, v in os.environ.items() if not k.startswith("RSI_")},
                )
                try:
                    async with (
                        stdio_client(parameters) as (reader, writer),
                        ClientSession(reader, writer) as session,
                    ):
                        await session.initialize()
                        result = await session.call_tool(
                            "rsi_create",
                            {
                                "request_id": "create",
                                "workspace": directory,
                                "message": "Write a marker. Begin implementation.",
                                "context": "HOST_CONTEXT_72: keep data semantics",
                            },
                        )
                        assert isinstance(result, CallToolResult)
                        self.assertFalse(result.is_error, result)
                        content = result.content[0]
                        assert isinstance(content, TextContent)
                        receipt = json.loads(content.text)
                        async with asyncio.timeout(30):
                            while receipt["status"] == "running":
                                receipt = await call(
                                    data,
                                    "rsi_request",
                                    {
                                        "request_id": "create",
                                        "wait_seconds": 20,
                                    },
                                )
                            self.assertEqual(receipt["status"], "completed", receipt)
                            run_id = receipt["result"]["run_id"]
                            while not blocked.is_set():
                                await asyncio.sleep(0.05)
                    # The host exits while Leader is waiting on its model, before the file write.
                    release.set()
                    cursor = 0
                    async with asyncio.timeout(30):
                        while True:
                            page = await call(
                                data,
                                "rsi_observe",
                                {
                                    "run_id": run_id,
                                    "cursor": cursor,
                                    "wait_seconds": 20,
                                },
                            )
                            self.assertNotEqual(page["session"]["status"], "failed", page)
                            cursor = page["cursor"]
                            if page["session"]["status"] == "completed":
                                break
                    self.assertEqual((root / "marker.txt").read_text(), "done")
                    self.assertIn("HOST_CONTEXT_72", json.dumps(model.requests[-1]))
                    self.assertFalse(steps or model.errors)
                finally:
                    release.set()
                    await stop_service(data)
        finally:
            model.close()


async def stop_service(root: Path) -> None:
    try:
        await call(root, "service_stop", {"force": True}, autostart=False)
    except (ValueError, ConnectionError):
        return
    async with asyncio.timeout(15):
        while socket_path(root).exists():
            await asyncio.sleep(0.05)
