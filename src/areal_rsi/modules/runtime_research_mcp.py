"""Native Codex MCP transport; authorization and execution stay in RSI."""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from areal_rsi.modules.runtime_tool_rpc import invoke


async def serve(config: dict[str, Any]) -> None:
    async def list_tools(context: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[
            types.Tool(**tool) for tool in config["tools"]
            if tool["name"] not in config.get("shell_only_tools", ())
        ])

    async def call_tool(context: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        try:
            value = await invoke(
                config,
                {
                    "name": params.name,
                    "arguments": params.arguments or {},
                    "call_id": uuid.uuid4().hex,
                },
            )
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=json.dumps(value, ensure_ascii=False))]
            )
        except ValueError as exc:
            return types.CallToolResult(
                is_error=True, content=[types.TextContent(type="text", text=str(exc))]
            )

    server: Server[Any] = Server("rsi", on_list_tools=list_tools, on_call_tool=call_tool)
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(serve(json.loads(Path(sys.argv[1]).read_text())))
