"""Installed, sandbox-inherited CLI for candidate stdio MCP tools."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from areal_agent_runtime.contracts import ToolCall, ToolResult, ToolSpec
from areal_agent_runtime.plugin_sdk import HealthStatus, PluginContext
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import JsonValue


async def invoke(servers: list[dict[str, Any]], arguments: list[str]) -> list[Any]:
    if not arguments or arguments[0] not in {"list", "call"}:
        raise ValueError("Expected list or call SERVER TOOL JSON_ARGUMENTS")
    if arguments[0] == "call" and len(arguments) != 4:
        raise ValueError("call needs SERVER TOOL JSON_ARGUMENTS")
    selected = (
        servers
        if arguments[0] == "list"
        else [s for s in servers if s["serverName"] == arguments[1]]
    )
    if not selected:
        raise ValueError("Unknown MCP server")
    result = []
    async with asyncio.timeout(60):
        for server in selected:
            if server.get("transport") != "stdio":
                raise ValueError("Candidate MCP supports stdio only")
            params = StdioServerParameters(
                command=server["command"], args=server["args"], cwd=server["cwd"]
            )
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                value = (
                    await session.list_tools()
                    if arguments[0] == "list"
                    else await session.call_tool(arguments[2], json.loads(arguments[3]))
                )
                result.append(
                    {"server": server["serverName"], "result": value.model_dump(mode="json")}
                )
    return result


if __name__ == "__main__":
    value = asyncio.run(invoke(json.loads(Path(sys.argv[1]).read_text()), sys.argv[2:]))
    encoded = json.dumps(value, ensure_ascii=False)
    if len(encoded) > 100000:
        raise ValueError("MCP response exceeds 100 KB")
    print(encoded)


class CandidateMcpProvider:
    """Application tools over validated RSI candidate servers, inside the OS sandbox."""

    def __init__(self, config: dict[str, Any]):
        self.servers = config["servers"]
        self.catalog = config["catalog"]

    async def list_tools(self) -> tuple[ToolSpec, ...]:
        return tuple(ToolSpec.model_validate(item["spec"]) for item in self.catalog)

    async def invoke(self, call: ToolCall) -> ToolResult:
        from areal_agent_runtime.contracts import ErrorCategory, ErrorInfo, TextBlock, ToolResult

        try:
            entry = next((item for item in self.catalog if item["spec"]["name"] == call.name), None)
            if entry is None:
                raise ValueError(f"Unknown MCP tool: {call.name}")
            values = await invoke(
                self.servers, ["call", entry["server"], entry["tool"], json.dumps(call.arguments)]
            )
            result = values[0]["result"]
            if result.get("is_error") or result.get("isError"):
                raise ValueError("MCP tool returned an error")
            blocks = result.get("content", [])
            if any(b.get("type") != "text" for b in blocks):
                raise ValueError("RSI candidate MCP projection currently accepts text results only")
            text = "\n".join(b["text"] for b in blocks)
            if len(text.encode()) > 100000:
                raise ValueError("MCP response exceeds 100 KB")
            return ToolResult(call_id=call.call_id, content=(TextBlock(text=text),))
        except Exception as exc:
            return ToolResult(
                call_id=call.call_id,
                error=ErrorInfo(
                    code="RSI_MCP_TOOL_FAILED", category=ErrorCategory.INTERNAL, message=str(exc)
                ),
            )

    async def health(self) -> HealthStatus:
        return HealthStatus(status="healthy")


class CandidateMcpPlugin:
    from areal_agent_runtime.plugin_sdk import PluginEntrypoints, PluginManifest, PortKey

    manifest = PluginManifest(
        name="rsi-candidate-mcp",
        version="1.0.0",
        implements=(PortKey.TOOL_PROVIDER,),
        entrypoints=PluginEntrypoints(local="areal_rsi.modules.runtime_mcp:create_plugin"),
        permissions=("process.spawn", "workspace.read", "workspace.write"),
        config_schema="runtime_mcp.schema.json",
    )

    async def configure(self, context: PluginContext, config: dict[str, JsonValue]) -> None:
        from areal_agent_runtime.plugin_sdk import PortKey

        self.provider = CandidateMcpProvider(config)
        context.provide(PortKey.TOOL_PROVIDER, self.provider)

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def health(self) -> HealthStatus:
        return await self.provider.health()


def create_plugin() -> CandidateMcpPlugin:
    return CandidateMcpPlugin()


async def discover(servers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    import hashlib

    catalog = []
    for server in await invoke(servers, ["list"]):
        for tool in server["result"]["tools"]:
            identity = server["server"] + "/" + tool["name"]
            name = "rsi_mcp_" + hashlib.sha256(identity.encode()).hexdigest()[:20]
            catalog.append(
                {
                    "server": server["server"],
                    "tool": tool["name"],
                    "spec": {
                        "name": name,
                        "description": identity + ": " + (tool.get("description") or ""),
                        "inputSchema": tool.get("input_schema", tool.get("inputSchema")),
                        "permissions": ["workspace.read", "workspace.write", "workspace.execute"],
                    },
                }
            )
            if len(catalog) > 128:
                raise ValueError("Candidate MCP catalog exceeds 128 tools")
    return catalog
