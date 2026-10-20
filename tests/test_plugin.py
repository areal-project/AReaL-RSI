"""Durable requests and a real MCP → Codex → filesystem goal."""

import asyncio
import contextlib
import json
import os
import shutil
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent
from native_model import Json, NativeModel, function

from areal_rsi.api.control import ServiceRequest
from areal_rsi.api.research import SessionStatus, UserPrompt
from areal_rsi.infra.service.handler import SessionService
from areal_rsi.infra.service.transport import call, socket_path


def test_duplicate_requests_and_restart_do_not_replay_work(tmp_path: Path) -> None:
    async def execute() -> None:
        service = SessionService(tmp_path / "data")
        create = ServiceRequest(
            "rsi_create",
            {
                "request_id": "create",
                "workspace": str(tmp_path),
                "mode": "chat",
                "message": "",
            },
        )

        async def pending(run_id: str, feedback: str) -> None:
            await asyncio.Event().wait()

        try:
            first, duplicate = await asyncio.gather(service.handle(create), service.handle(create))
            assert first == duplicate and first["status"] == "completed"
            assert len(service.system.store.list_sessions()) == 1
            with pytest.raises(ValueError, match="different arguments"):
                await service.handle(
                    ServiceRequest(
                        "rsi_create",
                        {**create.arguments, "message": "different"},
                    )
                )
            learn = ServiceRequest(
                "rsi_learn",
                {
                    "request_id": "learn",
                    "run_id": first["result"]["run_id"],
                },
            )
            with patch.object(service.system, "learn", side_effect=pending) as curator:
                assert (await service.handle(learn))["status"] == "running"
                await service.close()
                service = SessionService(tmp_path / "data")
                record = service.system.store.load(first["result"]["run_id"])
                record.status = SessionStatus.RUNNING
                record.pending_prompts = [UserPrompt(1, "question", "Old", prompt_id="expired")]
                service.system.store.save(record)
                service.recover()
                restored = service.system.store.load(record.run_id)
                assert restored.status == SessionStatus.PAUSED and not restored.pending_prompts
                assert await service.handle(create) == first
                assert (await service.handle(learn))["status"] == "interrupted"
                assert curator.await_count == 1 and not service.system.live
        finally:
            await service.close()

    asyncio.run(execute())


@pytest.mark.skipif(shutil.which("codex") is None, reason="Codex CLI required")
def test_native_goal_survives_mcp_disconnect(tmp_path: Path) -> None:
    blocked, release = threading.Event(), threading.Event()
    steps: list[str | list[Json]] = [
        function(
            "rsi_set_task",
            {
                "objective": "Write marker",
                "instructions": "Once",
                "acceptance": ["marker is done"],
            },
        ),
        function("rsi_start_goal", {}),
        "Handing off",
        function("exec_command", {"cmd": "printf done >> marker.txt"}),
        function(
            "rsi_update_goal",
            {
                "status": "completed",
                "summary": "Written",
                "evidence": ["marker.txt is done"],
            },
        ),
        "Finished",
    ]

    def respond(request: Json) -> str | list[Json]:
        if len(steps) == 3:
            blocked.set()
            assert release.wait(30), "Host did not disconnect"
        assert steps, "Unexpected model turn"
        return steps.pop(0)

    model = NativeModel(respond)
    data = tmp_path / "data"
    (tmp_path / ".env").write_text("RSI_API_KEY=offline-fixture\n")
    (tmp_path / "rsi.toml").write_text(
        f'[session]\nbase_url = "{model.url}"\nauto_learn = false\nkeep_awake = false\n'
    )

    async def execute() -> None:
        environment = {k: v for k, v in os.environ.items() if not k.startswith("RSI_")}
        environment.update(NO_PROXY="localhost,127.0.0.1,::1", no_proxy="localhost,127.0.0.1,::1")
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "areal_rsi", "mcp", "--data-root", str(data)],
            env=environment,
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
                        "workspace": str(tmp_path),
                        "message": "Write a marker. Begin.",
                        "context": "HOST_CONTEXT_72",
                    },
                )
                assert not result.is_error and isinstance(result.content[0], TextContent)
                receipt = json.loads(result.content[0].text)
                async with asyncio.timeout(40):
                    while receipt["status"] == "running":
                        receipt = await call(
                            data,
                            "rsi_request",
                            {
                                "request_id": "create",
                                "wait_seconds": 20,
                            },
                        )
                    assert receipt["status"] == "completed", receipt
                    while not blocked.is_set():
                        await asyncio.sleep(0.05)
            # Disconnect before execution, then observe through a separate client.
            release.set()
            cursor = 0
            async with asyncio.timeout(40):
                while True:
                    page = await call(
                        data,
                        "rsi_observe",
                        {
                            "run_id": receipt["result"]["run_id"],
                            "cursor": cursor,
                            "wait_seconds": 20,
                        },
                    )
                    assert page["session"]["status"] != "failed", page
                    cursor = page["cursor"]
                    if page["session"]["status"] == "completed":
                        break
            assert (tmp_path / "marker.txt").read_text() == "done"
            assert "HOST_CONTEXT_72" in json.dumps(model.requests[-1])
            assert not steps and not model.errors
        finally:
            release.set()
            with contextlib.suppress(ValueError, ConnectionError):
                await call(data, "service_stop", {"force": True}, autostart=False)
            async with asyncio.timeout(15):
                while socket_path(data).exists():
                    await asyncio.sleep(0.05)

    try:
        asyncio.run(execute())
    finally:
        release.set()
        model.close()
