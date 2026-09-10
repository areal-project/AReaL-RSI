"""Real pinned DSH tools, durable effects, and per-agent workspaces.

Set RSI_DSH_TOOLS_TEST_INSTALLATION to a prepared package directory to run the
Node integration cases. Ordinary unit tests never download npm packages.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from areal_rsi.modules.dsh_tools import DshToolWorker, definitions, tool_environment
from areal_rsi.modules.responses import ToolCall


@pytest.fixture
def installation() -> Path:
    configured = os.environ.get("RSI_DSH_TOOLS_TEST_INSTALLATION")
    if not configured or not shutil.which("node"):
        pytest.skip("Set RSI_DSH_TOOLS_TEST_INSTALLATION to run real DSH integration")
    path = Path(configured).resolve()
    assert (path / "node_modules/@deepseek-ai/dsh-tools/package.json").is_file()
    return path


def command(identifier: str, text: str, **kwargs: Any) -> ToolCall:
    return ToolCall("bash", {"command": text, "description": "检查测试环境", **kwargs}, identifier)


def test_environment_keeps_ssh_without_model_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RSI_API_KEY", "private-model-key")
    monkeypatch.setenv("CUSTOM_MODEL_CREDENTIAL", "private-custom-value")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/test/ssh-agent.sock")
    monkeypatch.setenv("BASH_ENV", "/dangerous/startup")
    monkeypatch.setenv("NODE_OPTIONS", "--inspect")
    result = tool_environment(
        {"WORKER_API_KEY": "also-private", "PROJECT_LANGUAGE": "zh"},
        credential_names=("CUSTOM_MODEL_CREDENTIAL",),
    )
    assert result["SSH_AUTH_SOCK"] == "/test/ssh-agent.sock"
    assert result["PROJECT_LANGUAGE"] == "zh"
    assert (
        not {"RSI_API_KEY", "CUSTOM_MODEL_CREDENTIAL", "BASH_ENV", "NODE_OPTIONS"} & result.keys()
    )
    assert "WORKER_API_KEY" not in result


def test_native_optional_parameters_stay_optional() -> None:
    tools = {item["name"]: item for item in definitions()}
    assert set(tools) == {"bash", "read", "write", "edit", "glob", "grep"}
    assert all(item["strict"] is False for item in tools.values())
    assert tools["bash"]["parameters"]["required"] == ["command", "description"]
    assert "run_in_background" not in tools["bash"]["parameters"]["properties"]
    assert "offset" not in tools["read"]["parameters"]["required"]


def test_real_file_tools_and_workspace_isolation(installation: Path, tmp_path: Path) -> None:
    async def scenario() -> None:
        one = DshToolWorker(installation, tmp_path / "one", tmp_path / "tools-one")
        two = DshToolWorker(installation, tmp_path / "two", tmp_path / "tools-two")
        env = tool_environment()
        try:
            first = await one.execute(
                ToolCall("write", {"file_path": "sample.txt", "content": "original\n"}, "create"),
                env,
            )
            assert not first["isError"]
            edit = ToolCall(
                "edit",
                {"file_path": "sample.txt", "old_string": "original", "new_string": "DSH"},
                "edit",
            )
            assert not (await one.execute(edit, env))["isError"]
            assert (tmp_path / "one/sample.txt").read_text() == "DSH\n"
            assert (await two.execute(edit, env))["isError"]
            assert not (tmp_path / "two/sample.txt").exists()
            matches = await one.execute(ToolCall("grep", {"pattern": "DSH"}, "grep"), env)
            assert "Line 1: DSH" in json.dumps(matches)
            paths = await one.execute(ToolCall("glob", {"pattern": "*.txt"}, "glob"), env)
            assert "sample.txt" in json.dumps(paths)
            outside = tmp_path / "environment.md"
            outside.write_text("Operator-provided setup guide")
            read = await one.execute(ToolCall("read", {"file_path": str(outside)}, "outside"), env)
            assert "Operator-provided setup guide" in json.dumps(read)
            source = one.workspace / "long.txt"
            source.write_text("".join(f"source line {i}\n" for i in range(1, 401)))
            read = await one.execute(
                ToolCall("read", {"file_path": "long.txt", "limit": 400}, "long"), env
            )
            assert not read["isError"]
            assert "source line 400" in json.dumps(read)
            source.write_text("x" * 40000)
            bounded = await one.execute(
                ToolCall("read", {"file_path": "long.txt"}, "bounded"), env
            )
            assert not bounded["isError"]
            assert "x" * 25000 not in json.dumps(bounded)
            pwd = await one.execute(command("pwd", "pwd"), env)
            assert pwd["value"]["stdout"]["text"].strip() == str(one.workspace)
        finally:
            await one.close()
            await two.close()

    asyncio.run(scenario())


def test_read_before_edit_and_external_changes(installation: Path, tmp_path: Path) -> None:
    async def scenario() -> None:
        worker = DshToolWorker(installation, tmp_path / "workspace", tmp_path / "tools")
        worker.workspace.mkdir()
        file = worker.workspace / "file.txt"
        file.write_text("old")
        env = tool_environment()
        try:
            args = {"file_path": "file.txt", "old_string": "old", "new_string": "new"}
            assert (await worker.execute(ToolCall("edit", args, "unread"), env))["isError"]
            await worker.execute(ToolCall("read", {"file_path": "file.txt"}, "read"), env)
            file.write_text("old plus external changes")
            assert (await worker.execute(ToolCall("edit", args, "stale"), env))["isError"]
            assert file.read_text() == "old plus external changes"
            await worker.execute(ToolCall("read", {"file_path": "file.txt"}, "reread"), env)
            assert not (await worker.execute(ToolCall("edit", args, "fresh"), env))["isError"]
            await worker.close()
            assert (await worker.execute(ToolCall("edit", args, "after-restart"), env))["isError"]
        finally:
            await worker.close()

    asyncio.run(scenario())


def test_completed_call_replays_without_executing_again(installation: Path, tmp_path: Path) -> None:
    async def scenario() -> None:
        worker = DshToolWorker(installation, tmp_path / "workspace", tmp_path / "tools")
        env = tool_environment()
        call = command("append-once", "printf x >> count.txt; cat count.txt")
        try:
            first = await worker.execute(call, env)
            assert first["value"]["exitCode"] == 0
            await worker.close()
            second = await worker.execute(call, env)
            assert second == first
            assert (worker.workspace / "count.txt").read_text() == "x"
            conflicting = await worker.execute(command("append-once", "printf y >> count.txt"), env)
            assert conflicting["isError"]
            assert (worker.workspace / "count.txt").read_text() == "x"
        finally:
            await worker.close()

    asyncio.run(scenario())


def test_unknown_outcome_does_not_repeat_side_effect(installation: Path, tmp_path: Path) -> None:
    async def scenario() -> None:
        state = tmp_path / "tools"
        receipts = state / "calls"
        receipts.mkdir(parents=True)
        call = command("lost", "printf duplicate > repeated.txt")
        fingerprint = json.dumps(
            [call.name, call.arguments], sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
        key = hashlib.sha256(call.call_id.encode()).hexdigest()
        (receipts / f"{key}.json").write_text(
            json.dumps({"status": "started", "fingerprint": fingerprint})
        )
        worker = DshToolWorker(installation, tmp_path / "workspace", state)
        try:
            result = await worker.execute(call, tool_environment())
            assert result["error"]["code"] == "OUTCOME_UNKNOWN"
            assert not (worker.workspace / "repeated.txt").exists()
        finally:
            await worker.close()

    asyncio.run(scenario())


def test_timeout_and_cancellation_terminate_shell(installation: Path, tmp_path: Path) -> None:
    async def scenario() -> None:
        worker = DshToolWorker(installation, tmp_path / "workspace", tmp_path / "tools")
        env = tool_environment()
        try:
            result = await worker.execute(command("fail", "printf problem >&2; exit 7"), env)
            assert result["value"]["exitCode"] == 7
            assert "problem" in result["value"]["stderr"]["text"]
            expired = await worker.execute(command("timeout", "sleep 10", timeoutMs=50), env)
            assert expired["value"]["timedOut"]
            call = command("cancel", "echo $$ > shell.pid; sleep 20; touch unexpected")
            running = asyncio.create_task(worker.execute(call, env))
            for _ in range(100):
                if (worker.workspace / "shell.pid").exists():
                    break
                await asyncio.sleep(0.01)
            pid = int((worker.workspace / "shell.pid").read_text())
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
            recovered = await worker.execute(call, env)
            assert recovered["error"]["info"]["code"] == "ABORTED"
            assert not (worker.workspace / "unexpected").exists()
        finally:
            await worker.close()

    asyncio.run(scenario())


def test_updated_environment_and_removed_credentials(installation: Path, tmp_path: Path) -> None:
    async def scenario() -> None:
        worker = DshToolWorker(installation, tmp_path / "workspace", tmp_path / "tools")
        try:
            for value in ("first", "changed"):
                env = tool_environment({"EXPERIMENT_MODE": value, "RSI_API_KEY": "secret"})
                result = await worker.execute(
                    command(value, 'test -z "$RSI_API_KEY" && printf %s "$EXPERIMENT_MODE"'), env
                )
                assert result["value"]["exitCode"] == 0
                assert result["value"]["stdout"]["text"] == value
        finally:
            await worker.close()

    asyncio.run(scenario())
