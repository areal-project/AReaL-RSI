import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

RUNTIME = Path(__file__).with_name("claude")


def environment(tmp_path, body):
    binary = tmp_path / "original-claude"
    binary.write_text("#!/bin/sh\n" + body)
    binary.chmod(0o700)
    return {
        **os.environ,
        "AREAL_CLAUDE_REAL_BINARY": str(binary),
        "ARENA_TASK_ID": "job:case:0",
        "ARENA_ATTEMPT": "1",
        "ARENA_OUTPUT_DIR": str(tmp_path / "out"),
        "ARENA_AGENT_TIMEOUT_SEC": "10.5",
        "AREAL_CLAUDE_DEADLINE_MARGIN_SEC": "10",
        "AREAL_CLAUDE_AGENT_TIMEOUT_SEC": "0.2",
    }


def test_normal_runtime_forwards_claude_protocol_and_arguments(tmp_path):
    env = environment(tmp_path, 'printf "%s\\n" "$*" >&2\ncat\n')
    result = subprocess.run(
        [sys.executable, str(RUNTIME), "--print", "--output-format", "stream-json"],
        input='{"type":"result","is_error":false}\n',
        capture_output=True,
        text=True,
        env=env,
        timeout=5,
    )
    assert result.returncode == 0
    assert result.stdout == '{"type":"result","is_error":false}\n'
    assert result.stderr.strip() == "--print --output-format stream-json"
    assert not (tmp_path / "out/harness_result.json").exists()


def test_own_deadline_terminates_child_and_writes_correlated_receipt(tmp_path):
    env = environment(tmp_path, "while :; do sleep 1; done\n")
    result = subprocess.run(
        [sys.executable, str(RUNTIME)],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert result.returncode == 124
    receipt = json.loads((tmp_path / "out/harness_result.json").read_text())
    assert receipt["status"] == "ERROR"
    assert receipt["raw"]["task_id"] == "job:case:0"
    assert receipt["raw"]["attempt"] == 1
    assert receipt["raw"]["outcome_code"] == "AGENT_RUN_TIMEOUT"
    assert receipt["raw"]["budget_seconds"] == 0.2
    assert receipt["raw"]["elapsed_seconds"] >= 0.2
    assert receipt["raw"]["child_returncode"] == -signal.SIGTERM


@pytest.mark.parametrize("exit_code", [1, 124, 137])
def test_child_failure_never_synthesizes_deadline(tmp_path, exit_code):
    env = environment(tmp_path, f"exit {exit_code}\n")
    result = subprocess.run([sys.executable, str(RUNTIME)], env=env, timeout=5)
    assert result.returncode == exit_code
    assert not (tmp_path / "out/harness_result.json").exists()


def test_external_cancel_is_not_an_execution_deadline(tmp_path):
    child_pid = tmp_path / "child.pid"
    env = environment(tmp_path, f"echo $$ > '{child_pid}'\nwhile :; do sleep 1; done\n")
    env["AREAL_CLAUDE_AGENT_TIMEOUT_SEC"] = "5"
    env["ARENA_AGENT_TIMEOUT_SEC"] = "20"
    process = subprocess.Popen([sys.executable, str(RUNTIME)], env=env)
    try:
        for _ in range(100):
            if child_pid.exists():
                break
            time.sleep(0.02)
        assert child_pid.exists()
        process.terminate()
        assert process.wait(timeout=10) == 128 + signal.SIGTERM
        with pytest.raises(ProcessLookupError):
            os.kill(int(child_pid.read_text()), 0)
        assert not (tmp_path / "out/harness_result.json").exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_invalid_budget_fails_before_starting_claude(tmp_path):
    marker = tmp_path / "started"
    env = environment(tmp_path, f"touch '{marker}'\n")
    env["AREAL_CLAUDE_AGENT_TIMEOUT_SEC"] = "7200"
    env["ARENA_AGENT_TIMEOUT_SEC"] = "7200"
    result = subprocess.run(
        [sys.executable, str(RUNTIME)],
        env=env,
        timeout=5,
        capture_output=True,
    )
    assert result.returncode == 1
    assert not marker.exists()
    assert not (tmp_path / "out/harness_result.json").exists()


def test_successful_retry_removes_previous_deadline_receipt(tmp_path):
    env = environment(tmp_path, "exit 0\n")
    output = tmp_path / "out"
    output.mkdir()
    (output / "harness_result.json").write_text(
        json.dumps({"raw": {"schema": "areal-claude-deadline/v1"}})
    )
    result = subprocess.run([sys.executable, str(RUNTIME)], env=env, timeout=5)
    assert result.returncode == 0
    assert not (output / "harness_result.json").exists()


def test_prepare_preserves_harness_and_declares_receipt_artifact(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "prepare_deadline_runtime", RUNTIME.with_name("prepare.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original = tmp_path / "claude"
    original.write_bytes(b"original-binary")
    base = {
        "spec": {
            "runtime": {"type": "claude_code", "uri": "old/claude", "digest": "old"},
            "agent": {"delivery": "builtin", "env": {"CLAUDE_TOOLS": "Read,Edit"}},
            "skill_refs": [{"key": "skill", "version": "1"}],
            "io_contract": {"artifact_globs": ["existing.log"]},
        }
    }
    module.prepare(base, original, tmp_path / "build", "new", "uploads/runtime/")
    payload = json.loads((tmp_path / "build/version.json").read_text())
    assert payload["spec"]["runtime"] == {"type": "claude_code", "uri": "uploads/runtime/"}
    assert payload["spec"]["agent"]["env"]["CLAUDE_TOOLS"] == "Read,Edit"
    assert payload["spec"]["skill_refs"] == base["spec"]["skill_refs"]
    assert payload["spec"]["io_contract"]["artifact_globs"] == [
        "existing.log",
        "$ARENA_OUTPUT_DIR/harness_result.json",
    ]
    assert base["spec"]["runtime"]["uri"] == "old/claude"
    assert (tmp_path / "build/runtime/claude.real").read_bytes() == b"original-binary"
