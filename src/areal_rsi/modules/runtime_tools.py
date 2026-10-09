"""Scoped RSI tools for the package Runtime and its native shell adapters.

This module is materialized inside each sandbox. It contains no model loop and
has no access to the coordinator: the parent authorizes every tool invocation.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from areal_agent_runtime.contracts import TextBlock, ToolCall, ToolResult, ToolSpec
from areal_agent_runtime.plugin_sdk import HealthStatus, PluginContext
from pydantic import JsonValue

FRESH_OBSERVATION_TOOLS = frozenset({"job_status", "wait_for_job"})


def shell_call_id(tool_name: str, explicit_call_id: str) -> str:
    """Keep mutations idempotent while ensuring repeated observations are fresh."""
    if tool_name in FRESH_OBSERVATION_TOOLS:
        return explicit_call_id + ":" + uuid.uuid4().hex
    return explicit_call_id


async def invoke(config: dict[str, Any], call: dict[str, Any]) -> Any:
    reader, writer = await asyncio.open_connection("127.0.0.1", config["port"], limit=2**20)
    try:
        writer.write((json.dumps({"token": config["token"], **call}) + "\n").encode())
        await writer.drain()
        response = json.loads(await reader.readline())
        if "error" in response:
            raise ValueError(response["error"])
        return response["output"]
    finally:
        writer.close()
        await writer.wait_closed()


class ResearchToolProvider:
    def __init__(self, config: dict[str, Any]):
        self.config = config

    async def list_tools(self) -> tuple[ToolSpec, ...]:
        return tuple(ToolSpec.model_validate(spec) for spec in self.config["tools"])

    async def invoke(self, call: ToolCall) -> ToolResult:
        value = await invoke(
            self.config,
            {"name": call.name, "arguments": call.arguments, "call_id": call.call_id},
        )
        return ToolResult(call_id=call.call_id, content=(TextBlock(text=json.dumps(value)),))

    async def health(self) -> HealthStatus:
        return HealthStatus(status="healthy")


class ResearchToolPlugin:
    from areal_agent_runtime.plugin_sdk import PluginEntrypoints, PluginManifest, PortKey

    manifest = PluginManifest(
        name="rsi-research-tools",
        version="1.0.0",
        implements=(PortKey.TOOL_PROVIDER,),
        entrypoints=PluginEntrypoints(local="areal_rsi.modules.runtime_tools:create_plugin"),
        permissions=("network.http",),
        config_schema="runtime_tools.schema.json",
    )

    async def configure(self, context: PluginContext, config: dict[str, JsonValue]) -> None:
        from areal_agent_runtime.plugin_sdk import PortKey

        self.provider = ResearchToolProvider(config)
        context.provide(PortKey.TOOL_PROVIDER, self.provider)

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def health(self) -> HealthStatus:
        return await self.provider.health()


def create_plugin() -> ResearchToolPlugin:
    return ResearchToolPlugin()


if __name__ == "__main__":
    config = json.loads(Path(sys.argv[1]).read_text())
    if sys.argv[2:] == ["list"]:
        print(json.dumps(config["tools"], ensure_ascii=False))
    elif len(sys.argv) == 6 and sys.argv[2] == "call":
        # Explicit call IDs make shell retries idempotent, just like native tools.
        value = asyncio.run(
            invoke(
                config,
                {
                    "name": sys.argv[3],
                    "arguments": json.loads(sys.argv[4]),
                    "call_id": shell_call_id(sys.argv[3], sys.argv[5]),
                },
            )
        )
        print(json.dumps(value, ensure_ascii=False))
    elif len(sys.argv) == 5 and sys.argv[2] == "call":
        value = asyncio.run(
            invoke(
                config,
                {
                    "name": sys.argv[3],
                    "arguments": json.loads(sys.argv[4]),
                    "call_id": uuid.uuid4().hex,
                },
            )
        )
        print(json.dumps(value, ensure_ascii=False))
    else:
        raise SystemExit("Expected list or call TOOL JSON_ARGUMENTS [CALL_ID]")
