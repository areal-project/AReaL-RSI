"""Claude Code Harness identity and calculator behavior."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from areal_rsi_tir.claude_backend import adapt_messages
from areal_rsi_tir.claude_calculator import calculate
from areal_rsi_tir.claude_code import (
    _claude_math_reward,
    _result,
    _training_claude_math_reward,
    freeze_claude_harness,
    load_claude_harness,
)
from areal_rsi_tir.harness_selection import select_agent_harnesses

from areal_rsi.api.records import AgentCaseRecord, AgentEvaluationReport, RsiSplit


def test_claude_reward_parses_fullwidth_digit_without_changing_answer() -> None:
    assert _claude_math_reward(r"\boxed{６}", "6") == 1.0
    assert _claude_math_reward(r"\boxed{７}", "6") == 0.0
    assert _claude_math_reward(r"\boxed{៣៣}", "33") == 1.0


def test_claude_reward_requires_final_boxed_answer() -> None:
    assert _claude_math_reward("USD budget: $0/$1; $1 remaining", "1") == 0.0
    assert _claude_math_reward("The answer is 1", "1") == 0.0
    assert _claude_math_reward(r"First guess \boxed{1}; final \boxed{2}", "1") == 0.0
    assert _claude_math_reward(r"First guess \boxed{1}; final \boxed{2}", "2") == 1.0
    assert _claude_math_reward(r"Final \boxed{\frac{1}{2}}", "1/2") == 1.0
    assert _claude_math_reward(r"Final \boxed{1", "1") == 0.0


def test_training_reward_contains_math_comparison_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from math_verify.errors import TimeoutException

    def timeout(response: str, answer: str) -> float:
        raise TimeoutException("undecidable comparison")

    monkeypatch.setattr("areal_rsi_tir.claude_code.strict_math_reward", timeout)
    assert _training_claude_math_reward(r"\boxed{\int x\,dx}", "0") == 0.0

    def malformed(response: str, answer: str) -> float:
        raise ValueError('String "٣٣" does not denote a Number')

    monkeypatch.setattr("areal_rsi_tir.claude_code.strict_math_reward", malformed)
    assert _training_claude_math_reward(r"\boxed{٣٣}", "33") == 0.0

    def invalid_gold(response: str, answer: str) -> float:
        raise ValueError("Frozen TIR gold answer cannot be parsed")

    monkeypatch.setattr("areal_rsi_tir.claude_code.strict_math_reward", invalid_gold)
    with pytest.raises(ValueError, match="Frozen TIR gold"):
        _training_claude_math_reward(r"\boxed{1}", "bad gold")


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
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "high"},
        "context_management": {"edits": []},
        "messages": [
            {"role": "system", "content": "CLI system"},
            {"role": "user", "content": "2+2"},
        ],
        "max_tokens": 32000,
    }
    adapted = json.loads(
        adapt_messages(
            json.dumps(request).encode(),
            max_tokens=4096,
            strip_unsupported_reasoning=True,
        )
    )
    assert adapted["messages"] == [{"role": "user", "content": "2+2"}]
    assert adapted["system"] == [
        {"type": "text", "text": "top level"},
        {"type": "text", "text": "CLI system"},
    ]
    assert adapted["max_tokens"] == 4096
    assert not {"thinking", "output_config", "context_management"} & adapted.keys()


def test_claude_harness_detects_prompt_and_cli_drift(tmp_path: Path) -> None:
    cli, prompt, frozen = tmp_path / "claude", tmp_path / "prompt.txt", tmp_path / "frozen"
    cli.write_bytes(b"fake executable")
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "config.json").write_text('{"model_type":"qwen2"}')
    (backend / "model.safetensors").write_bytes(b"fake weights")
    prompt.write_text("Solve carefully. User: {prompt}\\nAssistant:\\n")
    spec = freeze_claude_harness(prompt, cli, backend, frozen, model="qwen-test-pinned")
    assert load_claude_harness(frozen, cli, backend)[0] == spec
    (backend / "evaluation-logs").mkdir()
    (backend / "evaluation-logs/run.log").write_text("mutable output")
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


def test_parallel_agent_selection_uses_best_passing_candidate_and_fallback() -> None:
    digest = "sha256:" + "a" * 64

    def report(tag: str, scores: tuple[int, ...], tokens: int) -> AgentEvaluationReport:
        harness_id = "harness_" + tag * 64
        cases = tuple(
            AgentCaseRecord(
                str(index),
                harness_id,
                tag,
                digest,
                "answer",
                float(score),
                1,
                tokens,
                0.0,
                1,
                "/tmp/trace",
                digest,
            )
            for index, score in enumerate(scores)
        )
        return AgentEvaluationReport(
            harness_id,
            "taskset_test",
            RsiSplit.HARNESS_SELECT,
            "same-model",
            digest,
            digest,
            digest,
            tag,
            cases,
        )

    h0 = report("a", (1, 0, 0, 0), 10)
    repeat = AgentEvaluationReport(
        h0.harness_id,
        h0.task_set_id,
        h0.split,
        h0.model,
        h0.executable_digest,
        h0.backend_model_digest,
        h0.verifier_digest,
        "repeat",
        tuple(
            AgentCaseRecord(
                case.task_id,
                case.harness_id,
                "repeat",
                case.verifier_digest,
                case.response,
                case.score,
                case.input_tokens,
                case.output_tokens,
                case.cost_usd,
                case.duration_ms,
                case.transcript_path,
                case.transcript_digest,
            )
            for case in h0.cases
        ),
    )
    cheaper = report("b", (1, 1, 0, 0), 10)
    stronger = report("c", (1, 1, 1, 0), 10)
    costly = report("d", (1, 1, 1, 1), 13)
    result = select_agent_harnesses(h0, repeat, (cheaper, stronger, costly))
    assert result.selected_harness_id == stronger.harness_id
    assert result.continue_to_training
    assert [choice.accepted for choice in result.candidates] == [True, True, False]

    stopped = select_agent_harnesses(h0, repeat, (costly,), on_no_improvement="stop")
    assert stopped.selected_harness_id == h0.harness_id
    assert not stopped.continue_to_training
    reused = select_agent_harnesses(h0, repeat, (costly,))
    assert reused.selected_harness_id == h0.harness_id
    assert reused.continue_to_training
    no_candidates = select_agent_harnesses(h0, repeat, (), on_no_improvement="stop")
    assert not no_candidates.candidates
    assert not no_candidates.continue_to_training


@pytest.mark.parametrize("valid", [False, True])
def test_model_proposal_preserves_valid_output_and_rejects_invalid_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, valid: bool
) -> None:
    from areal_rsi_tir import claude_code
    from areal_rsi_tir.tasks import TirSplitSizes, freeze_tir_task_set, public_tir_tasks

    from areal_rsi.api.control import json_bytes

    train = tmp_path / "train.jsonl"
    test = tmp_path / "test.jsonl"
    train.write_text(
        "\n".join(
            json.dumps(
                {"messages": [{"role": "user", "content": f"What is {i}+1?"}], "answer": str(i + 1)}
            )
            for i in range(5)
        )
        + "\n"
    )
    test.write_text(
        json.dumps({"messages": [{"role": "user", "content": "What is 9+1?"}], "answer": "10"})
        + "\n"
    )
    task_root = tmp_path / "tasks"
    freeze_tir_task_set(train, test, task_root, sizes=TirSplitSizes(2, 1, 1, 1), seed=2)
    cli = tmp_path / "claude"
    cli.write_bytes(b"fake executable")
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "config.json").write_text('{"model_type":"qwen2"}')
    (backend / "model.safetensors").write_bytes(b"fake weights")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("System: Solve math.\nCheck arithmetic.\nUser: {prompt}\nAssistant:\n")
    harness = tmp_path / "harness"
    spec = freeze_claude_harness(prompt, cli, backend, harness, model="qwen-test")
    tasks = public_tir_tasks(task_root, RsiSplit.HARNESS_EVOLVE)
    digest = "sha256:" + "a" * 64
    cases = tuple(
        AgentCaseRecord(
            task.task_id,
            spec.harness_id,
            "baseline",
            digest,
            "A wrong result \\boxed{2}",
            0.0,
            10,
            10,
            0.0,
            1,
            "/tmp/trace",
            digest,
        )
        for task in tasks
    )
    manifest = json.loads((task_root / "manifest.json").read_bytes())
    baseline = AgentEvaluationReport(
        spec.harness_id,
        manifest["task_set_id"],
        RsiSplit.HARNESS_EVOLVE,
        spec.model,
        spec.executable_digest,
        spec.backend_model_digest,
        digest,
        "baseline",
        cases,
    )
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_bytes(json_bytes(baseline))
    instruction = "Verify the requested quantity and check signs before giving the final answer."
    raw = json_bytes(
        {
            "model": "qwen-test",
            "choices": [
                {
                    "text": instruction
                    if valid
                    else "Let's write Python code to solve an example.",
                    "finish_reason": "stop",
                }
            ],
        }
    )
    monkeypatch.setattr(claude_code, "complete_instruction", lambda *args, **kwargs: raw)
    for index in (0, 1):
        output = tmp_path / f"candidate-{index}"
        arguments = (
            task_root,
            baseline_path,
            harness,
            cli,
            backend,
            "http://127.0.0.1:31950",
            output,
        )
        if valid:
            claude_code.propose_claude_harness(*arguments, proposal_index=index)
            candidate = (output / "candidate.txt").read_text()
            assert "Check arithmetic." in candidate
            assert instruction in candidate
        else:
            with pytest.raises(ValueError, match="not a valid reusable solver instruction"):
                claude_code.propose_claude_harness(*arguments, proposal_index=index)
            assert not (output / "candidate.txt").exists()
            assert not (output / "harness").exists()
        origin = json.loads((output / "proposal-origin.json").read_bytes())
        assert origin["origin"] == ("same_model_proposal" if valid else "invalid_model_proposal")
        assert (output / "proposal.response.json").read_bytes() == raw
        assert (
            len(
                json.loads((output / "request.json").read_bytes())["public_failure_feedback"][
                    "sampled_task_ids"
                ]
            )
            == 2
        )


def test_claude_cli_evaluation_records_real_subprocess_trace_and_rejects_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from concurrent.futures import ThreadPoolExecutor

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
    (backend / "config.json").write_text('{"model_type":"qwen2"}')
    (backend / "model.safetensors").write_bytes(b"fake weights")
    cli = tmp_path / "fake-claude"
    cli.write_text(
        f"#!{sys.executable}\n"
        "import json,os,sys\n"
        "assert os.environ['ANTHROPIC_BASE_URL'].startswith('http://127.0.0.1:')\n"
        "assert os.environ['ANTHROPIC_API_KEY']=='local-inference-only'\n"
        "assert '--print' in sys.argv and '--strict-mcp-config' in sys.argv\n"
        "assert '--permission-mode' in sys.argv and 'dontAsk' in sys.argv\n"
        "assert '--allowedTools' in sys.argv and 'mcp__tir_calculator__calculate' in sys.argv\n"
        "assert '--system-prompt' in sys.argv and 'You solve math.' in sys.argv\n"
        "assert 'What is 1+1?' in sys.argv[-1]\n"
        "print(json.dumps({'type':'result','is_error':False,'result':'\\\\boxed{2}',"
        "'usage':{'input_tokens':100,'output_tokens':10},"
        "'total_cost_usd':0.002,'duration_ms':300}))\n"
    )
    cli.chmod(0o755)
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("System: You solve math.\nUser: {prompt}\nAssistant:\n")
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
    with ThreadPoolExecutor(max_workers=1) as pool:
        threaded = pool.submit(
            claude_code.evaluate_claude,
            task_root,
            harness_root,
            cli,
            backend,
            "http://127.0.0.1:31930",
            tmp_path / "thread-report.json",
            split=RsiSplit.MODEL_DEV,
            trial_id="threaded_trial",
            runner_root=tmp_path,
            workers=1,
        ).result()
    assert threaded.score == 1
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


def test_claude_timeout_scores_one_case_zero_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from areal_rsi_tir import claude_code
    from areal_rsi_tir.tasks import TirSplitSizes, freeze_tir_task_set, public_tir_tasks

    from areal_rsi.api.records import RsiSplit

    train, test = tmp_path / "train.jsonl", tmp_path / "test.jsonl"
    train.write_text(
        "\n".join(
            json.dumps(
                {
                    "messages": [{"role": "user", "content": f"What is 1+1? Variant {i}"}],
                    "answer": "2",
                }
            )
            for i in range(5)
        )
        + "\n"
    )
    test.write_text(
        json.dumps({"messages": [{"role": "user", "content": "2+2"}], "answer": "4"}) + "\n"
    )
    tasks = tmp_path / "tasks"
    freeze_tir_task_set(train, test, tasks, sizes=TirSplitSizes(2, 1, 1, 1), seed=2)
    first = public_tir_tasks(tasks, RsiSplit.HARNESS_EVOLVE)[0]
    cli = tmp_path / "fake-claude"
    cli.write_text(
        f"#!{sys.executable}\n"
        "import json,sys,time\n"
        f"if {first.input['messages'][0]['content']!r} in sys.argv[-1]: time.sleep(2)\n"
        "print(json.dumps({'type':'result','is_error':False,'result':'\\\\boxed{2}',"
        "'usage':{'input_tokens':10,'output_tokens':3},"
        "'total_cost_usd':0.0,'duration_ms':100}))\n"
    )
    cli.chmod(0o755)
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "config.json").write_text('{"model_type":"qwen2"}')
    (backend / "model.safetensors").write_bytes(b"fake weights")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Solve. User: {prompt}\nAssistant:\n")
    harness = tmp_path / "harness"
    freeze_claude_harness(prompt, cli, backend, harness, model="qwen-test", timeout_seconds=1)
    monkeypatch.setattr(claude_code, "source_files_digest", lambda _: "sha256:" + "a" * 64)
    monkeypatch.setattr(claude_code, "tir_verifier_files", lambda _: ())
    report = claude_code.evaluate_claude(
        tasks,
        harness,
        cli,
        backend,
        "http://127.0.0.1:31930",
        tmp_path / "report.json",
        split=RsiSplit.HARNESS_EVOLVE,
        trial_id="timeout_trial",
        runner_root=tmp_path,
        workers=2,
    )
    assert len(report.cases) == 2
    assert [case.score for case in report.cases] == [0.0, 1.0]
    assert report.cases[0].failure_reason == "cli_timeout"
    assert json.loads(Path(report.cases[0].transcript_path).read_bytes().splitlines()[-1]) == {
        "type": "local_timeout",
        "timeout_seconds": 1,
    }


def test_frozen_claude_workflow_uses_areal_session_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from areal_rsi_tir.claude_code import FrozenClaudeWorkflow
    from areal_rsi_tir.tasks import TirSplitSizes, freeze_tir_task_set, public_tir_tasks

    from areal_rsi.api.records import RsiSplit

    train, test = tmp_path / "train.jsonl", tmp_path / "test.jsonl"
    train.write_text(
        "\n".join(
            json.dumps(
                {
                    "messages": [{"role": "user", "content": f"What is 1+1? Variant {i}"}],
                    "answer": "2",
                }
            )
            for i in range(4)
        )
        + "\n"
    )
    test.write_text(
        json.dumps({"messages": [{"role": "user", "content": "2+2"}], "answer": "4"}) + "\n"
    )
    tasks = tmp_path / "tasks"
    task_set = freeze_tir_task_set(train, test, tasks, sizes=TirSplitSizes(1, 1, 1, 1), seed=2)
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "config.json").write_text('{"model_type":"qwen2"}')
    (backend / "model.safetensors").write_bytes(b"fake weights")
    cli = tmp_path / "fake-claude"
    cli.write_text(
        f"#!{sys.executable}\n"
        "import json,os,sys\n"
        "assert os.environ['ANTHROPIC_BASE_URL']=='http://proxy.example:3123'\n"
        "assert os.environ['ANTHROPIC_API_KEY']=='session-key'\n"
        "assert os.environ['CLAUDE_CODE_MAX_OUTPUT_TOKENS']=='4096'\n"
        "if os.environ.get('TIR_TEST_OVERFLOW')=='1':\n"
        " print(json.dumps({'type':'result','result':"
        '"API Error: Claude\'s response exceeded the 4096 output token maximum."}))\n'
        " sys.exit(1)\n"
        "if os.environ.get('TIR_TEST_NO_USAGE')=='1':\n"
        " print(json.dumps({'type':'result','is_error':False,'result':'\\\\boxed{2}'}))\n"
        " sys.exit(0)\n"
        "print(json.dumps({'type':'result','is_error':False,'result':'\\\\boxed{2}',"
        "'usage':{'input_tokens':10,'output_tokens':3},"
        "'total_cost_usd':0.0,'duration_ms':100}))\n"
    )
    cli.chmod(0o755)
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Solve. User: {prompt}\nAssistant:\n")
    harness = tmp_path / "harness"
    spec = freeze_claude_harness(prompt, cli, backend, harness, model="qwen-test")
    workflow = FrozenClaudeWorkflow(
        task_set_root=str(tasks),
        harness_root=str(harness),
        claude_cli=str(cli),
        backend_model_root=str(backend),
        task_set_id=task_set.task_set_id,
        harness_id=spec.harness_id,
    )
    task = public_tir_tasks(tasks, RsiSplit.MODEL_TRAIN)[0]
    row = {"task_id": task.task_id, "messages": task.input["messages"], "answer": "\\boxed{2}"}
    assert (
        asyncio.run(workflow.run(row, base_url="http://proxy.example:3123", api_key="session-key"))
        == 1.0
    )
    monkeypatch.setenv("TIR_TEST_OVERFLOW", "1")
    assert (
        asyncio.run(workflow.run(row, base_url="http://proxy.example:3123", api_key="session-key"))
        == 0.0
    )
    monkeypatch.delenv("TIR_TEST_OVERFLOW")
    monkeypatch.setenv("TIR_TEST_NO_USAGE", "1")
    assert (
        asyncio.run(workflow.run(row, base_url="http://proxy.example:3123", api_key="session-key"))
        == 1.0
    )
    with pytest.raises(ValueError, match="frozen dataset"):
        asyncio.run(
            workflow.run(
                {**row, "answer": "3"}, base_url="http://proxy.example:3123", api_key="session-key"
            )
        )
