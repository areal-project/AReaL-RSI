"""The shared runtime executes non-math MCP tools without importing the example."""

import os
import shlex
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

import pytest

from areal_rsi.api.execution import AgentRunResult, McpTool
from areal_rsi.api.records import AgentHarnessSpec
from areal_rsi.infra.runtime import run_process
from areal_rsi.infra.runtime.claude import read_result, run_claude


def test_timeout_stops_tool_child_that_ignores_termination(tmp_path: Path) -> None:
    child_pid = tmp_path / "child.pid"
    child = f'trap "" TERM; echo $$ > {shlex.quote(str(child_pid))}; exec sleep 60'
    parent = f"/bin/sh -c {shlex.quote(child)} & wait"
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            run_process(["/bin/sh", "-c", parent], timeout=1)
        pid = int(child_pid.read_text())
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            state = Path(f"/proc/{pid}/stat")
            if not state.exists() or state.read_text().split()[2] == "Z":
                break
            time.sleep(0.01)
        else:
            pytest.fail("Timed-out harness left its tool process running")
    finally:
        if child_pid.exists():
            with suppress(ProcessLookupError):
                os.kill(int(child_pid.read_text()), 9)


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


def test_claude_timeout_keeps_tool_events_and_final_shutdown_output(tmp_path: Path) -> None:
    from areal_rsi.api.execution import AgentExecutionSpec

    cli = tmp_path / "claude"
    cli.write_text(
        f"#!{sys.executable}\n"
        "import signal,sys,time\n"
        "def stop(*_args):\n"
        "    print('shutdown event', flush=True)\n"
        "    sys.exit(1)\n"
        "signal.signal(signal.SIGTERM,stop)\n"
        "print('tool event', flush=True)\n"
        "time.sleep(60)\n"
    )
    cli.chmod(0o755)
    with pytest.raises(subprocess.TimeoutExpired) as failure:
        run_claude(
            cli,
            AgentExecutionSpec("test-model", 1, 1.0),
            "hello",
            McpTool("text", "uppercase", sys.executable, ()),
            "http://127.0.0.1:1",
        )
    assert failure.value.output == b"tool event\nshutdown event\n"


def test_adapter_reaches_private_backend_despite_parent_proxy(monkeypatch) -> None:
    import json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from urllib.request import ProxyHandler, Request, build_opener

    from areal_rsi.infra.runtime.anthropic import local_adapter

    requests = []

    class Backend(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

    # 127.0.0.2 is reachable locally but deliberately absent from NO_PROXY.
    upstream = ThreadingHTTPServer(("127.0.0.2", 0), Backend)
    thread = Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    try:
        with local_adapter(f"http://127.0.0.2:{upstream.server_port}", max_tokens=32) as url:
            request = Request(
                url + "/v1/messages",
                data=b'{"max_tokens":100,"messages":[{"role":"user","content":"hello"}]}',
                headers={"Content-Type": "application/json"},
            )
            with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
                assert json.load(response) == {"ok": True}
        assert requests == [{"max_tokens": 32, "messages": [{"role": "user", "content": "hello"}]}]
    finally:
        upstream.shutdown()
        upstream.server_close()
        thread.join()


def test_native_bench_claude_uses_all_tools_except_search_and_agents(
    tmp_path: Path, monkeypatch
) -> None:
    from areal_rsi.api.execution import AgentExecutionSpec
    from areal_rsi.infra.runtime.claude import read_result

    for name in (
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
        "CLAUDE_CODE_MAX_CONTEXT_TOKENS",
        "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE",
    ):
        monkeypatch.setenv(name, "old-limit")
    cli = tmp_path / "claude"
    cli.write_text(
        f"#!{sys.executable}\n"
        "import json,os,sys\n"
        "args=sys.argv[1:]\n"
        "assert args[args.index('--permission-mode')+1]=='bypassPermissions'\n"
        "assert args[args.index('--disallowedTools')+1]=='WebSearch,Agent'\n"
        "assert not {'--tools','--allowedTools','--max-turns','--max-budget-usd',"
        "'--no-session-persistence','--no-chrome','--setting-sources',"
        "'--strict-mcp-config','--system-prompt'} & set(args)\n"
        "assert not any(n in os.environ for n in ('CLAUDE_CODE_MAX_OUTPUT_TOKENS',"
        "'CLAUDE_CODE_MAX_CONTEXT_TOKENS','CLAUDE_AUTOCOMPACT_PCT_OVERRIDE'))\n"
        "print(json.dumps({'type':'result','is_error':False,'result':os.getcwd(),'usage':{'input_tokens':1,'output_tokens':1},'total_cost_usd':0,'duration_ms':1}))\n"
    )
    cli.chmod(0o755)
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    transcript = tmp_path / "stream.jsonl"
    raw = run_claude(
        cli,
        AgentExecutionSpec("test-model"),
        "System: preserve this user prompt\nTask",
        McpTool("workspace", "workspace", sys.executable, ()),
        "http://127.0.0.1:1",
        cwd=candidate,
        transcript_path=transcript,
    )
    assert read_result(raw).response == str(candidate)
    assert transcript.read_bytes() == raw
