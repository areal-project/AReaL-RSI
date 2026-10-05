"""The shared runtime executes non-math MCP tools without importing the example."""

import sys
from pathlib import Path

from areal_rsi.api.execution import AgentRunResult, McpTool
from areal_rsi.api.records import AgentHarnessSpec
from areal_rsi.infra.runtime.claude import read_result, run_claude


def test_runtime_routes_a_nonmath_tool_and_decodes_result(tmp_path: Path) -> None:
    tool = tmp_path / "uppercase.py"
    tool.write_text("import sys\nprint(sys.stdin.read().upper())\n")
    cli = tmp_path / "claude"
    cli.write_text(
        f"#!{sys.executable}\n"
        "import json, subprocess, sys\n"
        "args = sys.argv[1:]\n"
        "assert args[args.index('--allowedTools')+1] == 'mcp__text__uppercase'\n"
        "assert args[args.index('--tools')+1] == ''\n"
        "config = json.load(open(args[args.index('--mcp-config')+1]))\n"
        "server = config['mcpServers']['text']\n"
        "result = subprocess.check_output([server['command'], *server['args']], "
        "input=args[-1], text=True).strip()\n"
        "print(json.dumps({'type':'result', 'is_error':False, 'result':result, "
        "'usage':{'input_tokens':1, 'output_tokens':1}, "
        "'total_cost_usd':0, 'duration_ms':1}))\n"
    )
    cli.chmod(0o755)
    digest = "sha256:" + "a" * 64
    spec = AgentHarnessSpec(
        "harness_" + "a" * 64, digest, digest, digest, digest, "test-model", 10, 1.0
    )
    raw = run_claude(
        cli,
        spec,
        "hello",
        McpTool("text", "uppercase", sys.executable, (str(tool),)),
        "http://127.0.0.1:1",
    )
    assert read_result(raw) == AgentRunResult("HELLO", 1, 1, 0.0, 1)


def test_budget_exhaustion_preserves_transcript_and_other_errors_still_raise(
    tmp_path: Path,
) -> None:
    import json

    import pytest

    from areal_rsi.infra.runtime.claude import ClaudeBudgetLimit

    cli = tmp_path / "claude"
    digest = "sha256:" + "a" * 64
    spec = AgentHarnessSpec(
        "harness_" + "a" * 64, digest, digest, digest, digest, "test-model", 10, 1.0
    )
    tool = McpTool("text", "uppercase", sys.executable, ())
    event = {
        "type": "result",
        "subtype": "error_max_budget_usd",
        "is_error": True,
        "errors": ["Reached maximum budget ($1)"],
        "num_turns": 54,
        "stop_reason": "tool_use",
    }
    raw = (json.dumps(event) + "\n").encode()
    cli.write_text(
        f"#!{sys.executable}\nimport sys\nsys.stdout.buffer.write({raw!r})\nsys.exit(1)\n"
    )
    cli.chmod(0o755)
    with pytest.raises(ClaudeBudgetLimit) as failure:
        run_claude(cli, spec, "hello", tool, "http://127.0.0.1:1")
    assert failure.value.transcript == raw
    cli.write_text(
        f"#!{sys.executable}\nimport sys\nsys.stderr.write('connection refused')\nsys.exit(1)\n"
    )
    with pytest.raises(RuntimeError, match="connection refused") as failure:
        run_claude(cli, spec, "hello", tool, "http://127.0.0.1:1")
    assert not isinstance(failure.value, ClaudeBudgetLimit)
