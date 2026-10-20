"""MCP is a transport: the detached service owns agents, goals and experience."""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path
from typing import Any

import mcp.server.stdio
import mcp.types as types
from mcp.server import Server, ServerRequestContext

from areal_rsi.api.control import OPERATIONS, decode_record, public_tool_text, record_schema
from areal_rsi.infra.service.transport import call


def server(root: Path) -> Server[None]:
    async def list_tools(
        ctx: ServerRequestContext[Any],
        params: types.PaginatedRequestParams | None,
    ) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=name,
                    description=spec.description,
                    input_schema=record_schema(spec.arguments),
                    annotations=types.ToolAnnotations(
                        read_only_hint=not spec.mutating, idempotent_hint=True
                    ),
                )
                for name, spec in OPERATIONS.items()
            ]
        )

    async def call_tool(
        ctx: ServerRequestContext[Any],
        params: types.CallToolRequestParams,
    ) -> types.CallToolResult:
        try:
            spec = OPERATIONS.get(params.name)
            if spec is None:
                raise ValueError("Unknown RSI tool")
            arguments = params.arguments or {}
            decode_record(spec.arguments, arguments)
            result = await call(root, params.name, arguments)
            failed = result.get("status") in {"failed", "interrupted"}
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=public_tool_text(result, 60000))],
                is_error=failed,
            )
        except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=public_tool_text(str(exc)))],
                is_error=True,
            )

    return Server(
        "areal-rsi",
        version=version("areal-rsi"),
        instructions="RSI owns an independent runtime. Forward user context, messages and answers; "
        "observe progress without duplicating its work. Save run_id, event cursor and request_id. "
        "Poll running receipts with rsi_request; reuse the same ID and arguments "
        "after timeouts. Never treat acceptance as goal completion or invent user approval.",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


async def serve(root: Path) -> None:
    application = server(root)
    async with mcp.server.stdio.stdio_server() as (reader, writer):
        await application.run(reader, writer, application.create_initialization_options())
