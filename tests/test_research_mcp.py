"""Real MCP stdio -> HTTP -> ResearchApp, with only model inference replaced."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

from areal_rsi.modules.responses import ResponsesConfig, ResponsesRuntime
from areal_rsi.research_app import ResearchApp
from areal_rsi.ui.workbench import WorkbenchServer


class WaitTransport:
    async def request(self, *_: Any) -> dict[str, Any]:
        return {
            "id": "fixture",
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "name": "wait",
                    "call_id": "fixture-wait",
                    "arguments": '{"reason":"等待用户要求"}',
                }
            ],
            "usage": {"total_tokens": 1},
        }


def test_native_mcp_controls_new_research_without_old_workflow(tmp_path: Path) -> None:
    app = ResearchApp(
        tmp_path,
        runtime_factory=lambda _: ResponsesRuntime(
            ResponsesConfig(base_url="https://example.test", api_key="fixture"), WaitTransport()
        ),
    )
    server = WorkbenchServer(app, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    async def scenario() -> None:
        params = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "areal_rsi.ui.mcp",
                "--root",
                str(tmp_path),
                "--port",
                str(server.server_port),
            ],
            env={
                **os.environ,
                "RSI_TEACHER_MODEL": "fixture",
                "RSI_TEACHER_BASE_URL": "https://example.test",
                "RSI_TEACHER_API_KEY": "private-fixture-key",
            },
        )
        async with (
            stdio_client(params) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
            assert tools == {"rsi_plan", "rsi_start", "rsi_status", "rsi_control", "rsi_message"}

            async def call(name: str, values: dict[str, Any]) -> dict[str, Any]:
                result = await session.call_tool(name, values)
                assert not result.is_error, result
                assert "private-fixture-key" not in result.model_dump_json()
                block = result.content[0]
                assert isinstance(block, TextContent)
                return json.loads(block.text)  # type: ignore[no-any-return]

            plan = await call("rsi_plan", {"goal": "只准备一个通用研究", "max_research_agents": 1})
            assert plan["status"] == "planned" and plan["budget"]["model_calls"] == 0
            run_id = plan["run_id"]
            assert (await call("rsi_status", {}))["run_id"] == run_id
            started = await call("rsi_start", {})
            assert started["status"] == "running"
            message = await call(
                "rsi_message", {"text": "请等待，不要启动外部作业", "message_id": "user-fixture"}
            )
            duplicate = await call(
                "rsi_message", {"text": "请等待，不要启动外部作业", "message_id": "user-fixture"}
            )
            assert message["message_id"] == duplicate["message_id"]
            assert (await call("rsi_control", {"action": "pause"}))["status"] == "paused"
            assert (await call("rsi_control", {"action": "resume"}))["status"] == "running"
            assert (await call("rsi_control", {"action": "stop"}))["status"] == "stopped"
            record = json.loads((tmp_path / "runs" / run_id / "run.json").read_text())
            assert record["plan"]["workflow"] == "research"
            messages = [
                json.loads(p.read_text())
                for p in (tmp_path / "runs" / run_id / "messages").glob("*.json")
            ]
            assert any(m["sender"] == "user" and m["recipient"] == "rsi" for m in messages)
            assert not (tmp_path / "run-states").exists()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run_coroutine_threadsafe(app.close(), server.loop).result(timeout=5)
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
