"""Scoped RSI tools for the package Runtime and its native shell adapters.

This module is materialized inside each sandbox. It contains no model loop and
has no access to the coordinator: the parent authorizes every tool invocation.
"""

from __future__ import annotations

import json
from typing import Any

from areal_agent_runtime.contracts import TextBlock, ToolCall, ToolResult, ToolSpec
from areal_agent_runtime.plugin_sdk import HealthStatus, PluginContext
from pydantic import JsonValue

from areal_rsi.modules.runtime_tool_rpc import invoke as invoke


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
