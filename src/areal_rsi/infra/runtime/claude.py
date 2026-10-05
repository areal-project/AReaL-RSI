"""Task-independent Claude Code subprocess execution and result decoding."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from contextlib import nullcontext
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from areal_rsi.api.control import json_bytes
from areal_rsi.api.execution import AgentRunResult, McpTool
from areal_rsi.api.records import AgentHarnessSpec
from areal_rsi.infra.runtime.anthropic import local_adapter

CLAUDE_MAX_OUTPUT_TOKENS = 4096
COMPACT_BACKEND_MAX_TOKENS = 512


class ClaudeOutputLimit(RuntimeError):
    def __init__(self, transcript: bytes) -> None:
        super().__init__("Claude Code exhausted its output token budget")
        self.transcript = transcript


class ClaudeBudgetLimit(RuntimeError):
    def __init__(self, transcript: bytes) -> None:
        super().__init__("Claude Code exhausted its monetary budget")
        self.transcript = transcript


def final_event(raw: bytes) -> dict[str, Any]:
    events = [json.loads(line) for line in raw.splitlines() if line.strip()]
    final = [event for event in events if isinstance(event, dict) and event.get("type") == "result"]
    if len(final) != 1 or final[0].get("is_error") is not False:
        raise ValueError("Claude Code did not return one successful final result")
    return final[0]


def read_result(raw: bytes) -> AgentRunResult:
    event = final_event(raw)
    usage = event.get("usage")
    if not isinstance(usage, dict):
        raise ValueError("Claude Code omitted token usage")
    response = event.get("result")
    input_tokens, output_tokens = usage.get("input_tokens"), usage.get("output_tokens")
    cost, duration = event.get("total_cost_usd"), event.get("duration_ms")
    if (
        not isinstance(response, str)
        or not response.strip()
        or type(input_tokens) is not int
        or type(output_tokens) is not int
        or not isinstance(cost, (float, int))
        or isinstance(cost, bool)
        or type(duration) is not int
    ):
        raise ValueError("Claude Code returned incomplete result metadata")
    return AgentRunResult(response, input_tokens, output_tokens, float(cost), duration)


def run_claude(
    executable: Path,
    spec: AgentHarnessSpec,
    prompt: str,
    tool: McpTool,
    backend_url: str,
    *,
    api_key: str | None = None,
) -> bytes:
    parsed = urlparse(backend_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or (
            api_key is None
            and (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"})
        )
        or (api_key is not None and not api_key)
    ):
        raise ValueError("Claude Code backend must be local HTTP or an authenticated proxy")
    with tempfile.TemporaryDirectory(prefix="rsi-claude-case-") as temporary:
        work = Path(temporary)
        mcp = work / "mcp.json"
        mcp.write_bytes(
            json_bytes(
                {
                    "mcpServers": {
                        tool.server: {
                            "command": tool.command,
                            "args": list(tool.arguments),
                        }
                    }
                }
            )
        )
        compact_system: str | None = None
        if prompt.startswith("System: "):
            first_line, prompt = prompt.split("\n", 1)
            compact_system = first_line.removeprefix("System: ").strip()
            if not compact_system:
                raise ValueError("Frozen Claude system prompt is empty")
        command = [
            str(executable),
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--no-session-persistence",
            "--no-chrome",
            "--strict-mcp-config",
            "--setting-sources",
            "",
            "--mcp-config",
            str(mcp),
            "--permission-mode",
            "dontAsk",
            "--tools",
            "",
            "--allowedTools",
            f"mcp__{tool.server}__{tool.name}",
            "--model",
            spec.model,
            "--max-budget-usd",
            str(spec.max_budget_usd),
        ]
        if compact_system is not None:
            command.extend(("--system-prompt", compact_system))
        command.append(prompt)
        env = os.environ.copy()
        for name in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
        ):
            env.pop(name, None)
        env.update(
            {
                "ANTHROPIC_BASE_URL": backend_url,
                "ANTHROPIC_API_KEY": api_key or "local-inference-only",
                "NO_PROXY": "*",
                "no_proxy": "*",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(CLAUDE_MAX_OUTPUT_TOKENS),
            }
        )
        adapter = (
            local_adapter(backend_url, max_tokens=COMPACT_BACKEND_MAX_TOKENS)
            if compact_system is not None
            else nullcontext(backend_url)
        )
        with adapter as local_url:
            env["ANTHROPIC_BASE_URL"] = local_url
            completed = subprocess.run(
                command,
                env=env,
                cwd=work,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=spec.timeout_seconds,
                check=False,
            )
        if completed.returncode != 0:
            result_events = []
            for line in completed.stdout.splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and event.get("type") == "result":
                    result_events.append(event)
            if result_events and result_events[-1].get("subtype") == "error_max_budget_usd":
                raise ClaudeBudgetLimit(completed.stdout)
            result_error = str(result_events[-1].get("result", "")) if result_events else ""
            if (
                f"Claude's response exceeded the {CLAUDE_MAX_OUTPUT_TOKENS} output token maximum"
                in result_error
            ):
                raise ClaudeOutputLimit(completed.stdout)
            raise RuntimeError(
                f"Claude Code exited {completed.returncode}: "
                f"{result_error[:1000]} {completed.stderr.decode(errors='replace')[:1000]}"
            )
        return completed.stdout
