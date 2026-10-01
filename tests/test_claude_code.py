"""Claude Code Harness identity and calculator behavior."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from areal_rsi_tir.claude_backend import adapt_messages
from areal_rsi_tir.claude_calculator import calculate
from areal_rsi_tir.claude_code import _result, freeze_claude_harness, load_claude_harness


def test_calculator_mcp_stdio_handles_exact_math_and_rejects_code() -> None:
    server = Path(__file__).resolve().parents[1] / "examples/areal_rsi_tir/claude_calculator.py"
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "calculate", "arguments": {"expression": "(2+1)/2"}},
        },
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "calculate",
                "arguments": {"expression": "__import__('os').system('true')"},
            },
        },
    ]
    result = subprocess.run(
        [sys.executable, str(server)],
        input="\n".join(json.dumps(req) for req in requests) + "\n",
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
    )
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    assert [reply["id"] for reply in replies] == [1, 2, 3, 4]
    assert replies[1]["result"]["tools"][0]["name"] == "calculate"
    assert replies[2]["result"]["content"][0]["text"] == "3/2"
    assert replies[3]["result"]["isError"] is True
    with pytest.raises(ValueError, match="Division by zero"):
        calculate("1/0")


def test_local_backend_adapts_claude_system_message_and_output_limit() -> None:
    request = {
        "system": [{"type": "text", "text": "top level"}],
        "messages": [
            {"role": "system", "content": "CLI system"},
            {"role": "user", "content": "2+2"},
        ],
        "max_tokens": 32000,
    }
    adapted = json.loads(adapt_messages(json.dumps(request).encode(), max_tokens=4096))
    assert adapted["messages"] == [{"role": "user", "content": "2+2"}]
    assert adapted["system"] == [
        {"type": "text", "text": "top level"},
        {"type": "text", "text": "CLI system"},
    ]
    assert adapted["max_tokens"] == 4096


def test_claude_harness_detects_prompt_and_cli_drift(tmp_path: Path) -> None:
    cli, prompt, frozen = tmp_path / "claude", tmp_path / "prompt.txt", tmp_path / "frozen"
    cli.write_bytes(b"fake executable")
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "model.safetensors").write_bytes(b"fake weights")
    prompt.write_text("Solve carefully. User: {prompt}\\nAssistant:\\n")
    spec = freeze_claude_harness(prompt, cli, backend, frozen, model="qwen-test-pinned")
    assert load_claude_harness(frozen, cli, backend)[0] == spec
    cli.write_bytes(b"different executable")
    with pytest.raises(ValueError, match="changed"):
        load_claude_harness(frozen, cli, backend)


def test_claude_result_requires_success_and_usage() -> None:
    event = {
        "type": "result",
        "is_error": False,
        "result": "\\boxed{3}",
        "usage": {"input_tokens": 20, "output_tokens": 8},
        "total_cost_usd": 0.001,
        "duration_ms": 500,
    }
    raw = (
        json.dumps({"type": "assistant", "message": {"content": []}}).encode()
        + b"\n"
        + json.dumps(event).encode()
    )
    assert _result(raw) == ("\\boxed{3}", 20, 8, 0.001, 500)
    event["is_error"] = True
    with pytest.raises(ValueError, match="successful"):
        _result(json.dumps(event).encode())


def test_claude_cli_evaluation_records_real_subprocess_trace_and_rejects_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from areal_rsi_tir import claude_code
    from areal_rsi_tir.tasks import TirSplitSizes, freeze_tir_task_set

    from areal_rsi.api.records import RsiSplit

    train, test = tmp_path / "train.jsonl", tmp_path / "test.jsonl"

    def row(i: int) -> str:
        return json.dumps(
            {"messages": [{"role": "user", "content": f"What is 1+1? Variant {i}"}], "answer": "2"}
        )

    train.write_text("\n".join(row(i) for i in range(4)) + "\n")
    test.write_text(row(4) + "\n")
    task_root = tmp_path / "taskset"
    freeze_tir_task_set(train, test, task_root, sizes=TirSplitSizes(1, 1, 1, 1), seed=2)
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "model.safetensors").write_bytes(b"fake weights")
    cli = tmp_path / "fake-claude"
    cli.write_text(
        f"#!{sys.executable}\n"
        "import json,os,sys\n"
        "assert os.environ['ANTHROPIC_BASE_URL']=='http://127.0.0.1:31930'\n"
        "assert os.environ['ANTHROPIC_API_KEY']=='local-inference-only'\n"
        "assert '--print' in sys.argv and '--strict-mcp-config' in sys.argv\n"
        "assert '--permission-mode' in sys.argv and 'dontAsk' in sys.argv\n"
        "assert '--allowedTools' in sys.argv and 'mcp__tir_calculator__calculate' in sys.argv\n"
        "assert 'What is 1+1?' in sys.argv[-1]\n"
        "print(json.dumps({'type':'result','is_error':False,'result':'\\\\boxed{2}',"
        "'usage':{'input_tokens':100,'output_tokens':10},"
        "'total_cost_usd':0.002,'duration_ms':300}))\n"
    )
    cli.chmod(0o755)
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Solve the question. User: {prompt}\nAssistant:\n")
    harness_root = tmp_path / "harness"
    freeze_claude_harness(prompt, cli, backend, harness_root, model="qwen-test-pinned")
    monkeypatch.setattr(claude_code, "source_files_digest", lambda _: "sha256:" + "a" * 64)
    monkeypatch.setattr(claude_code, "tir_verifier_files", lambda _: ())
    output = tmp_path / "report.json"
    kwargs = dict(split=RsiSplit.MODEL_DEV, trial_id="trial1", runner_root=tmp_path)
    report = claude_code.evaluate_claude(
        task_root, harness_root, cli, backend, "http://127.0.0.1:31930", output, **kwargs
    )
    assert report.score == 1
    assert report.cases[0].output_tokens == 10
    assert Path(report.cases[0].transcript_path).is_file()
    assert (
        claude_code.evaluate_claude(
            task_root, harness_root, cli, backend, "http://127.0.0.1:31930", output, **kwargs
        )
        == report
    )
    Path(report.cases[0].transcript_path).write_text("tampered")
    with pytest.raises(ValueError, match="trace changed"):
        claude_code.evaluate_claude(
            task_root, harness_root, cli, backend, "http://127.0.0.1:31930", output, **kwargs
        )
