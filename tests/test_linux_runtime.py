"""Opt-in Linux worker execution, including the real package/tool bridge."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from areal_rsi.api.research import TaskBrief
from areal_rsi.coordination.actions import definition
from areal_rsi.coordination.config import ResearchConfig
from areal_rsi.modules.agent_runtime import ToolCall, ToolResult
from areal_rsi.modules.research_runtime import ResearchRuntime
from areal_rsi.modules.runtime_package import RuntimeSettings, sandbox_command


def test_isolation_requires_explicit_supported_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RSI_RUNTIME_ISOLATION", raising=False)
    assert RuntimeSettings({}).isolation == "sandbox"
    with patch("sys.platform", "linux"), pytest.raises(RuntimeError, match="linux-bwrap"):
        sandbox_command(tmp_path, tmp_path, ())
    monkeypatch.setenv("RSI_RUNTIME_ISOLATION", "typo")
    with pytest.raises(ValueError, match="RSI_RUNTIME_ISOLATION"):
        RuntimeSettings({})
    with patch("sys.platform", "darwin"):
        with pytest.raises(RuntimeError, match="only supported on Linux"):
            sandbox_command(tmp_path, tmp_path, (), isolation="linux-unrestricted")
        command = sandbox_command(tmp_path, tmp_path, ())
        assert command[:2] == ["/usr/bin/sandbox-exec", "-f"]
        assert "(deny file-write*)" in (tmp_path / "sandbox.sb").read_text()


@pytest.mark.skipif(
    sys.platform != "linux" or not __import__("shutil").which("bwrap"),
    reason="Linux Bubblewrap integration",
)
def test_linux_bwrap_hides_host_tree_and_keeps_workspace_writable(tmp_path: Path) -> None:
    session = tmp_path / "session"
    workspace = tmp_path / "workspace"
    private = tmp_path / "private"
    for path in (session / "worker-code", workspace, private):
        path.mkdir(parents=True)
    (private / "answer.txt").write_text("hidden")
    command = sandbox_command(session, workspace, (), isolation="linux-bwrap")
    separator = len(command) - 1 - command[::-1].index("--")
    probe = subprocess.run(
        [
            *command[: separator + 1],
            "/bin/bash",
            "-ec",
            f"test ! -e {private}; "
            "node -e \"if(typeof require('node:util').parseEnv !== 'function') process.exit(1)\"; "
            "printf ok > probe.txt",
        ],
        text=True,
        capture_output=True,
    )
    assert probe.returncode == 0, probe.stderr
    assert (workspace / "probe.txt").read_text() == "ok"


@pytest.mark.skipif(
    sys.platform != "linux" or not __import__("shutil").which("bwrap"),
    reason="Linux Bubblewrap integration",
)
def test_linux_bwrap_overlays_readonly_workspace_file(tmp_path: Path) -> None:
    session, workspace = tmp_path / "session", tmp_path / "workspace"
    source, target = tmp_path / "published.jsonl", workspace / "data/pool.jsonl"
    (session / "worker-code").mkdir(parents=True)
    target.parent.mkdir(parents=True)
    target.touch()
    source.write_text("published\n")
    command = sandbox_command(
        session,
        workspace,
        (),
        readonly_mounts=((source, target),),
        isolation="linux-bwrap",
    )
    separator = len(command) - 1 - command[::-1].index("--")
    probe = subprocess.run(
        [
            *command[: separator + 1],
            "/bin/bash",
            "-ec",
            'test "$PWD" = /workspace; '
            'test "$(cat /workspace/data/pool.jsonl)" = published; '
            f'test "$(cat {target})" = published; '
            "! printf changed > data/pool.jsonl 2>/dev/null",
        ],
        text=True,
        capture_output=True,
    )
    assert probe.returncode == 0, probe.stderr
    assert target.read_text() == ""


@pytest.mark.skipif(sys.platform != "linux", reason="Linux worker integration")
def test_linux_worker_shell_tools_and_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RSI_RUNTIME_ISOLATION", "linux-unrestricted")
    monkeypatch.setenv("RSI_API_KEY", "fixture")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:54321")
    monkeypatch.setenv("UNRELATED_API_KEY", "must-not-be-inherited")

    async def scenario() -> None:
        runtime = ResearchRuntime(
            ResearchConfig(TaskBrief("test", "test")),
            settings=RuntimeSettings({"default": {"kind": "fake"}}),
        )
        calls: list[str] = []
        call_ids: list[str] = []

        async def handle(call: ToolCall) -> ToolResult:
            calls.append(call.name)
            call_ids.append(call.call_id)
            return ToolResult({"answer": 42})

        session = tmp_path / "session"
        processes: list[asyncio.subprocess.Process] = []
        try:
            for index in range(2):
                result = await runtime.turn(
                    session,
                    system_prompt="test",
                    user_message=f"turn {index}",
                    message_id=f"message-{index}",
                    tools=[definition("get_state", "State")],
                    handler=handle,
                    workspace=tmp_path / "workspace",
                )
                assert result.text
                request = json.loads((session / "runtime/request.json").read_text())
                assert request["isolation"] == "linux-unrestricted"
                assert not (session / "runtime/sandbox.sb").exists()
                helper = session / "runtime/bin/rsi-tools"
                process = await asyncio.create_subprocess_exec(
                    "/bin/bash",
                    "-ec",
                    'cat > probe.txt <<EOF\nshell works\nEOF\n"$1" call get_state "{}" "$2"',
                    "bash",
                    str(helper),
                    "reused-native-call-id",
                    cwd=tmp_path / "workspace",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await process.communicate()
                assert process.returncode == 0, stderr.decode()
                assert "42" in stdout.decode()
                assert (tmp_path / "workspace/probe.txt").read_text() == "shell works\n"
                worker = runtime.conversations[session].session.process
                raw_environment = (Path("/proc") / str(worker.pid) / "environ").read_text()
                environment = dict(
                    item.split("=", 1) for item in raw_environment.split("\0") if "=" in item
                )
                assert environment["SHELL"] == "/bin/bash"
                assert environment["HTTPS_PROXY"] == "http://127.0.0.1:54321"
                assert environment["NODE_USE_ENV_PROXY"] == "1"
                assert "UNRELATED_API_KEY" not in environment
                processes.append(worker)
                await runtime.close()
                assert worker.returncode is not None
            assert calls == ["get_state", "get_state"]
            assert len({p.pid for p in processes}) == 2
            assert len(set(call_ids)) == 2
        finally:
            await runtime.close()

    asyncio.run(scenario())
