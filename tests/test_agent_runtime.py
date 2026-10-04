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
