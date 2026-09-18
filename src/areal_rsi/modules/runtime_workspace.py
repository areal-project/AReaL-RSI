"""Workspace tools for explicitly authorized Linux execution without isolation."""

from __future__ import annotations

import copy
import shlex
import sys
from pathlib import Path

from areal_agent_runtime.adapters.workspace import LocalWorkspaceToolProvider, WorkspaceToolConfig
from areal_agent_runtime.contracts import ErrorCategory, ErrorInfo, ToolCall, ToolResult, ToolSpec
from areal_agent_runtime.plugin_sdk import (
    HealthStatus,
    PluginContext,
    PluginEntrypoints,
    PluginManifest,
    PortKey,
)
from pydantic import JsonValue


class LinuxWorkspaceTools:
    """Adapt public bounded tools while preserving each agent's relative working directory."""

    def __init__(self) -> None:
        self.provider = LocalWorkspaceToolProvider(
            WorkspaceToolConfig(
                allowed_commands=("bash",), command_timeout_seconds=60, max_output_chars=32000
            )
        )

    async def list_tools(self) -> tuple[ToolSpec, ...]:
        tools = []
        for tool in await self.provider.list_tools():
            schema = copy.deepcopy(tool.input_schema)
            properties = schema.get("properties")
            for key in ("path", "cwd"):
                field = properties.get(key) if isinstance(properties, dict) else None
                if isinstance(field, dict):
                    field["description"] = (
                        "Absolute host path or path relative to this agent's workspace"
                    )
            description = (
                "Run argv with the service user's permissions; absolute executables are supported. "
                "Use bash -c for shell syntax. Commands time out after 60 seconds."
                if tool.name == "workspace.run"
                else tool.description + " Absolute host paths are supported in this trusted mode."
            )
            tools.append(
                tool.model_copy(update={"description": description, "input_schema": schema})
            )
        return tuple(tools)

    async def invoke(self, call: ToolCall) -> ToolResult:
        if call.name not in {
            "workspace.read",
            "workspace.write",
            "workspace.edit",
            "workspace.list",
            "workspace.search",
            "workspace.run",
        }:
            return await self.provider.invoke(call)
        arguments = copy.deepcopy(call.arguments)
        try:
            if not call.workspace.mount_hint:
                raise ValueError("Workspace mount is required")
            key = "cwd" if call.name == "workspace.run" else "path"
            original_path = arguments.get(key, ".")
            if not isinstance(original_path, str) or not original_path or "\0" in original_path:
                raise ValueError("Path must be a non-empty string without NUL bytes")
            path = Path(original_path)
            if not path.is_absolute():
                path = Path(call.workspace.mount_hint) / path
            # Resolve symlinks before delegating to the provider's descriptor-based I/O.
            arguments[key] = path.resolve().relative_to("/").as_posix()
            original_argv = arguments.get("argv")
            if call.name == "workspace.run":
                if not isinstance(original_argv, list) or not 1 <= len(original_argv) <= 256:
                    raise ValueError("argv must contain 1–256 non-empty strings, at most 128 KiB")
                argv: list[str] = []
                for value in original_argv:
                    if not isinstance(value, str) or not value or "\0" in value:
                        raise ValueError("argv entries must be non-empty strings without NUL bytes")
                    argv.append(value)
                if sum(len(value.encode()) for value in argv) > 128 * 1024:
                    raise ValueError("argv exceeds 128 KiB")
                # Quoting preserves argument boundaries and the requested executable path.
                # exec retains the provider's process-group timeout/cancellation semantics.
                arguments["argv"] = ["bash", "-c", "exec " + shlex.join(argv)]
            mapped = call.model_copy(
                update={
                    "workspace": call.workspace.model_copy(update={"mount_hint": "/"}),
                    "arguments": arguments,
                }
            )
            result = await self.provider.invoke(mapped)
            if call.name == "workspace.run" and isinstance(result.value, dict):
                result = result.model_copy(
                    update={"value": {**result.value, "argv": original_argv}}
                )
            return result
        except (ValueError, OSError) as exc:
            return ToolResult(
                call_id=call.call_id,
                error=ErrorInfo(
                    code="WORKSPACE_INVALID_REQUEST",
                    category=ErrorCategory.INVALID_REQUEST,
                    message=str(exc),
                ),
            )

    async def health(self) -> HealthStatus:
        return HealthStatus(status="healthy", details={"isolation": "linux-unrestricted"})


class LinuxWorkspacePlugin:
    manifest = PluginManifest(
        name="rsi-linux-workspace",
        version="1.0.0",
        implements=(PortKey.TOOL_PROVIDER,),
        entrypoints=PluginEntrypoints(local="areal_rsi.modules.runtime_workspace:create_plugin"),
        config_schema="runtime_workspace.schema.json",
        permissions=("workspace.read", "workspace.write", "workspace.execute"),
    )

    async def configure(self, context: PluginContext, config: dict[str, JsonValue]) -> None:
        if sys.platform != "linux" or config.get("unrestricted") is not True:
            raise ValueError("Linux workspace tools require explicit unrestricted mode")
        self.provider = LinuxWorkspaceTools()
        context.provide(PortKey.TOOL_PROVIDER, self.provider)

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def health(self) -> HealthStatus:
        return await self.provider.health()


def create_plugin() -> LinuxWorkspacePlugin:
    return LinuxWorkspacePlugin()
