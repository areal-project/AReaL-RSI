from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from areal_agent_runtime.adapters.workspace import LocalWorkspaceToolProvider
from areal_agent_runtime.contracts import ToolCall

from areal_rsi.modules.runtime_workspace import LinuxWorkspaceTools


def tool_call(workspace: Path, name: str, arguments: dict) -> ToolCall:
    return ToolCall.model_validate(
        {
            "callId": "test",
            "runId": "run",
            "sessionId": "session",
            "executionId": "execution",
            "agentId": "agent",
            "name": name,
            "arguments": arguments,
            "workspace": {"workspaceId": "workspace", "mountHint": str(workspace)},
            "agentEnvironment": {
                "snapshotId": "env_empty",
                "digest": "0" * 64,
                "specDigest": "0" * 64,
                "promptResolution": "fallback",
            },
        }
    )


@pytest.mark.skipif(sys.platform != "linux", reason="Linux tools")
def test_absolute_paths_executables_and_relative_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (external / "input.txt").write_text("external input")
    (workspace / "link").symlink_to(external, target_is_directory=True)

    async def scenario() -> None:
        provider = LinuxWorkspaceTools()
        for path in [str(external / "input.txt"), "link/input.txt"]:
            call = tool_call(workspace, "workspace.read", {"path": path})
            result = await provider.invoke(call)
            assert result.error is None
            assert result.value["content"] == "external input"
            assert (await LocalWorkspaceToolProvider().invoke(call)).error is not None

        for executable in ["bash", "/bin/bash"]:
            argv = [executable, "-c", 'printf "%s" "$1"; pwd', "test", "$(touch should-not-exist)"]
            result = await provider.invoke(tool_call(workspace, "workspace.run", {"argv": argv}))
            assert result.error is None
            assert result.value["argv"] == argv
            assert "$(touch should-not-exist)" in result.value["stdout"]
            assert str(workspace) in result.value["stdout"]
            assert not (workspace / "should-not-exist").exists()

        result = await provider.invoke(
            tool_call(
                workspace,
                "workspace.run",
                {
                    "argv": [sys.executable, "-c", "import pathlib;print(pathlib.Path.cwd())"],
                    "cwd": str(external),
                },
            )
        )
        assert result.error is None
        assert str(external) in result.value["stdout"]
        result = await provider.invoke(
            tool_call(
                workspace,
                "workspace.write",
                {
                    "path": "local.txt",
                    "content": "own workspace",
                },
            )
        )
        assert result.error is None
        assert (workspace / "local.txt").read_text() == "own workspace"

    asyncio.run(scenario())


@pytest.mark.parametrize("discovery", ["workspace.list", "workspace.search"])
@pytest.mark.parametrize("location", ["relative", "external", "symlink"])
def test_returned_paths_can_be_reused(tmp_path: Path, discovery: str, location: str) -> None:
    # Path translation and descriptor-based I/O can also be checked on macOS;
    # only enabling the unrestricted plugin is Linux-specific.
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (workspace / "link").symlink_to(external, target_is_directory=True)
    directory = workspace / "local" if location == "relative" else external
    directory.mkdir(exist_ok=True)
    target = directory / "input.txt"
    target.write_text("original content")
    requested = {"relative": "local", "external": str(external), "symlink": "link"}[location]

    async def scenario() -> None:
        provider = LinuxWorkspaceTools()
        arguments = {"path": requested}
        if discovery == "workspace.search":
            arguments["query"] = "original"
        result = await provider.invoke(tool_call(workspace, discovery, arguments))
        assert result.error is None
        entries = result.value["entries" if discovery == "workspace.list" else "matches"]
        assert len(entries) == 1
        returned_path = entries[0]["path"]
        # Feed each tool's output into the next without repairing the path.
        result = await provider.invoke(
            tool_call(workspace, "workspace.read", {"path": returned_path})
        )
        assert result.error is None
        assert result.value["content"] == "original content"
        assert returned_path == str(target.resolve())
        result = await provider.invoke(
            tool_call(
                workspace,
                "workspace.edit",
                {"path": result.value["path"], "oldText": "original", "newText": "edited"},
            )
        )
        assert result.error is None
        assert target.read_text() == "edited content"
        result = await provider.invoke(
            tool_call(
                workspace,
                "workspace.write",
                {"path": result.value["path"], "content": "rewritten content"},
            )
        )
        assert result.error is None
        result = await provider.invoke(
            tool_call(workspace, "workspace.read", {"path": result.value["path"]})
        )
        assert result.error is None
        assert result.value["content"] == target.read_text() == "rewritten content"
        assert result.value["path"] == str(target.resolve())
        assert arguments["path"] == requested

    asyncio.run(scenario())
