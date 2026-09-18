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
