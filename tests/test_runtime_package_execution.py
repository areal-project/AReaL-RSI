"""Real package/SDK processes against a deterministic local model; no paid inference."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from areal_rsi.api.base import AgentSpec, ModelVersion
from areal_rsi.base.storage import FileContentStore, FileTrajectoryStore, now
from areal_rsi.modules.runtime_package import PackageAgentRuntime, RuntimeSettings


class ScriptedModel:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.results: list[str] = []
        self.final_text: Any = lambda request: "RSI_FINAL_OK"
        self.before_reply: Any = lambda: None
        self.force_mcp = False
        self.stream_fragments: list[str] | None = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append(request)
                owner.before_reply()
                if "messages" not in request:
                    self.send_error(404, "Scripted endpoint supports Chat Completions and Messages")
                    return
                messages = request["messages"]
                chat = self.path.endswith("/chat/completions")
                tools = request.get("tools", [])
                result_seen = any(
                    m.get("role") == "tool"
                    or (
                        isinstance(m.get("content"), list)
                        and any(b.get("type") == "tool_result" for b in m["content"])
                    )
                    for m in messages
                )
                if result_seen:
                    owner.results.append(
                        json.dumps(
                            [m for m in messages if m.get("role") == "tool"]
                            + [
                                b
                                for m in messages
                                if isinstance(m.get("content"), list)
                                for b in m["content"]
                                if b.get("type") == "tool_result"
                            ]
                        )
                    )
                tool = None
                for item in tools:
                    t = item.get("function", item)
                    if "workspace_run" in t["name"] or t["name"].split(".")[-1] in (
                        "run",
                        "workspace_run",
                        "exec_command",
                        "terminal",
                        "shell",
                        "Bash",
                    ):
                        tool = t
                        break
                if owner.force_mcp:
                    tool = next(
                        (
                            t.get("function", t)
                            for t in tools
                            if "rsi_mcp_" in t.get("name", t.get("function", {}).get("name", ""))
                        ),
                        None,
                    )
                if not result_seen and tool:
                    schema = tool.get("input_schema", tool.get("parameters", {}))
                    properties = schema.get("properties", {})
                    command = "printf RSI_TOOL_OK"
                    args: dict[str, Any]
                    if owner.force_mcp:
                        args = {"expression": "6 * 7"}
                    elif "argv" in properties:
                        args = {"argv": ["sh", "-c", command]}
                    elif "cmd" in properties:
                        args = {"cmd": command}
                    elif properties.get("command", {}).get("type") == "array":
                        args = {"command": ["sh", "-c", command]}
                    else:
                        args = {"command": command}
                    blocks = [
                        {
                            "type": "tool_use",
                            "id": "call_proof",
                            "name": tool["name"],
                            "input": args,
                        }
                    ]
                    stop = "tool_use"
                else:
                    blocks = [{"type": "text", "text": owner.final_text(request)}]
                    stop = "end_turn"
                self.send_response(200)
                if chat and request.get("stream"):
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    delta = (
                        {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_proof",
                                    "type": "function",
                                    "function": {
                                        "name": blocks[0]["name"],
                                        "arguments": json.dumps(blocks[0]["input"]),
                                    },
                                }
                            ]
                        }
                        if stop == "tool_use"
                        else {"content": owner.final_text(request)}
                    )
                    choices: list[dict[str, Any]] = [
                        {"delta": delta, "finish_reason": None},
                        {
                            "delta": {},
                            "finish_reason": "tool_calls" if stop == "tool_use" else "stop",
                        },
                    ]
                    if stop != "tool_use" and owner.stream_fragments is not None:
                        # Many small deltas expose quadratic event persistence costs.
                        choices = [
                            {"delta": {"content": part}, "finish_reason": None}
                            for part in owner.stream_fragments
                        ] + [{"delta": {}, "finish_reason": "stop"}]
                    for choice in choices:
                        event = {
                            "id": "completion-proof",
                            "object": "chat.completion.chunk",
                            "model": request["model"],
                            "choices": [{"index": 0, **choice}],
                        }
                        self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
                        if owner.stream_fragments is not None:
                            self.wfile.flush()
                            time.sleep(0.005)
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    self.close_connection = True
                elif chat:
                    message: dict[str, Any] = {
                        "role": "assistant",
                        "content": owner.final_text(request),
                    }
                    if stop == "tool_use":
                        message = {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_proof",
                                    "type": "function",
                                    "function": {
                                        "name": blocks[0]["name"],
                                        "arguments": json.dumps(blocks[0]["input"]),
                                    },
                                }
                            ],
                        }
                    body = json.dumps(
                        {
                            "id": "completion-proof",
                            "object": "chat.completion",
                            "created": 1,
                            "model": request["model"],
                            "choices": [
                                {
                                    "index": 0,
                                    "message": message,
                                    "finish_reason": "tool_calls" if stop == "tool_use" else "stop",
                                }
                            ],
                            "usage": {
                                "prompt_tokens": 10,
                                "completion_tokens": 5,
                                "total_tokens": 15,
                            },
                        }
                    ).encode()
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    body = json.dumps(
                        {
                            "id": "msg-proof",
                            "type": "message",
                            "role": "assistant",
                            "model": request["model"],
                            "content": blocks,
                            "stop_reason": stop,
                            "stop_sequence": None,
                            "usage": {"input_tokens": 10, "output_tokens": 5},
                        }
                    ).encode()
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


@unittest.skipUnless(
    importlib.util.find_spec("anthropic"), "Install the anthropic extra for SDK proof"
)
class RealPackageExecutionTest(unittest.IsolatedAsyncioTestCase):
    async def check_harness(
        self, kind: str, config: dict[str, Any], *, stream_fragments: list[str] | None = None
    ) -> None:
        model_server = ScriptedModel()
        model_server.stream_fragments = stream_fragments
        expected = "".join(stream_fragments) if stream_fragments is not None else "RSI_FINAL_OK"
        model_server.final_text = lambda request: expected
        try:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp).resolve()
                content = FileContentStore(root / "content")
                trajectories = FileTrajectoryStore(root / "trajectories", content)
                runtime = PackageAgentRuntime(
                    root / "sessions",
                    content,
                    trajectories,
                    settings=RuntimeSettings(
                        {
                            "default": {"kind": kind, "config": config},
                            "readable_paths": [config["hermesRoot"]] if kind == "hermes" else [],
                        }
                    ),
                    role="task-agent",
                    dsh_root=Path(os.environ["RSI_TEST_DSH_ROOT"])
                    if os.environ.get("RSI_TEST_DSH_ROOT")
                    else None,
                    base_url=model_server.url,
                    api_key="local-proof",
                    timeout=90,
                )
                model = ModelVersion(
                    "model-proof", "test-model", await content.put_text("model"), now()
                )
                session = await runtime.open(
                    AgentSpec(
                        "task-agent",
                        "deepseek-harness@v1",
                        model,
                        (await content.put_text("Use a shell tool, then give a final answer."),),
                        tools=("bash@v1",),
                    )
                )
                try:
                    self.assertEqual(await session.send("Execute printf RSI_TOOL_OK"), expected)
                    self.assertTrue(any("RSI_TOOL_OK" in result for result in model_server.results))
                    self.assertIn("tool.completed", [e["type"] for e in session.events])
                except BaseException:
                    for log in session.directory.rglob("*.log"):
                        print(str(log), log.read_text(errors="replace")[-4000:])
                    print(
                        "model requests:",
                        [
                            (r.get("tools", []), r.get("messages", [])[-1:])
                            for r in model_server.requests
                        ],
                    )
                    raise
                finally:
                    await session.finish()
                # finish() reloads committed events, so also check the durable export.
                self.assertTrue((session.directory / "data/state").is_dir())
                self.assertEqual(list((session.directory / "data").rglob("*.sqlite*")), [])
                self.assertIn("run.completed", [e["type"] for e in session.events])
                self.assertIn("tool.completed", [e["type"] for e in session.events])
                if stream_fragments is not None:
                    chunks = [
                        e["data"]["delta"]["text"]
                        for e in session.events
                        if e["type"] == "message.delta"
                        and e["data"].get("delta", {}).get("type") == "text"
                    ]
                    self.assertEqual(chunks, stream_fragments)
        finally:
            await asyncio.to_thread(model_server.close)

    async def test_anthropic_tool_roundtrip(self) -> None:
        await self.check_harness("anthropic", {})

    @unittest.skipUnless(
        os.environ.get("RSI_TEST_NATIVE_HARNESSES") == "1", "Opt in to native DSH installation"
    )
    async def test_dsh_tool_roundtrip(self) -> None:
        from deepseek_harness_runtime import bundled_runtime_path  # type: ignore[import-untyped]

        await self.check_harness("dsh", {"dshBin": str(bundled_runtime_path()), "profile": "sdk"})

    @unittest.skipUnless(
        os.environ.get("RSI_TEST_NATIVE_HARNESSES") == "1", "Opt in to native DSH installation"
    )
    async def test_dsh_streaming_events_are_complete_and_durable(self) -> None:
        from deepseek_harness_runtime import bundled_runtime_path

        await self.check_harness(
            "dsh",
            {"dshBin": str(bundled_runtime_path()), "profile": "sdk"},
            stream_fragments=[f"fragment-{index:04d} " for index in range(2000)],
        )

    @unittest.skipUnless(
        os.environ.get("RSI_TEST_NATIVE_HARNESSES") == "1", "Opt in to native Codex execution"
    )
    async def test_codex_tool_roundtrip(self) -> None:
        if not shutil.which("codex"):
            self.skipTest("Official Codex CLI is not installed")
        await self.check_harness(
            "codex-cli", {"modelProtocol": "anthropic", "codexBin": shutil.which("codex")}
        )

    async def test_cancel_commits_terminal_and_reaps_worker(self) -> None:
        from areal_rsi.modules.runtime_storage import read_journal

        model_server = ScriptedModel()
        release = threading.Event()
        model_server.before_reply = lambda: release.wait(20)
        try:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp).resolve()
                content = FileContentStore(root / "content")
                runtime = PackageAgentRuntime(
                    root / "sessions",
                    content,
                    FileTrajectoryStore(root / "trajectories", content),
                    settings=RuntimeSettings({"default": {"kind": "anthropic"}}),
                    role="task-agent",
                    base_url=model_server.url,
                    api_key="local",
                    timeout=30,
                )
                model = ModelVersion("m", "fixture", await content.put_text("model"), now())
                session = await runtime.open(
                    AgentSpec("task-agent", "areal-agent-runtime@v1", model)
                )
                task = asyncio.create_task(session.send("wait"))
                async with asyncio.timeout(15):
                    while not model_server.requests:
                        await asyncio.sleep(0.02)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                await session.finish()
                self.assertIsNotNone(session.process.returncode)
                self.assertTrue((session.directory / "data/state").is_dir())
                self.assertEqual(list((session.directory / "data").rglob("*.sqlite*")), [])
                _events, snapshots = read_journal(session.directory / "data/state")
                snapshot = snapshots.get(session.run_ids[-1])
                assert snapshot is not None
                self.assertEqual(snapshot.status, "cancelled")
                self.assertIn("run.cancelled", [e["type"] for e in session.events])
        finally:
            release.set()
            await asyncio.to_thread(model_server.close)

    @unittest.skip("User requested skipping local Claude Code execution")
    async def test_claude_code_execution(self) -> None:
        await self.check_harness("claude-code", {})

    @unittest.skip("Claude SDK also launches Claude Code; local execution excluded")
    async def test_claude_sdk_execution(self) -> None:
        await self.check_harness("claude-agent-sdk", {})

    async def test_candidate_mcp_is_an_application_tool(self) -> None:
        import sys

        import areal_rsi

        model_server = ScriptedModel()
        model_server.force_mcp = True
        try:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp).resolve()
                workspace = root / "workspace"
                shutil.copytree(Path(areal_rsi.__file__).parent / "default_harness", workspace)
                content = FileContentStore(root / "content")
                runtime = PackageAgentRuntime(
                    root / "sessions",
                    content,
                    FileTrajectoryStore(root / "trajectories", content),
                    settings=RuntimeSettings({"default": {"kind": "anthropic"}}),
                    role="task-agent",
                    base_url=model_server.url,
                    api_key="local",
                )
                runtime.mcp_registry["math"] = {
                    "serverName": "arithmetic",
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [str(workspace / "tools/math_mcp.py")],
                    "cwd": str(workspace),
                }
                model = ModelVersion("m", "fixture", await content.put_text("model"), now())
                session = await runtime.open(
                    AgentSpec("task-agent", "areal-agent-runtime@v1", model, mcp_servers=("math",)),
                    workspace_root=str(workspace),
                )
                try:
                    self.assertEqual(
                        await session.send("Use the arithmetic MCP tool"), "RSI_FINAL_OK"
                    )
                    self.assertTrue(any("42" in r for r in model_server.results))
                    for request in model_server.requests:
                        names = [
                            tool.get("function", tool)["name"]
                            for tool in request.get("tools", [])
                        ]
                        self.assertTrue(names)
                        self.assertTrue(all(name.startswith("rsi_mcp_") for name in names), names)
                    completed = [e for e in session.events if e["type"] == "tool.completed"]
                    self.assertEqual(len(completed), 1)
                    self.assertIn("rsi_mcp_", json.dumps(completed))
                finally:
                    await session.finish()
        finally:
            await asyncio.to_thread(model_server.close)

    @unittest.skipUnless(
        os.environ.get("RSI_TEST_HERMES_ROOT"), "Set actual Hermes installation root"
    )
    async def test_hermes_tool_roundtrip(self) -> None:
        root = Path(os.environ["RSI_TEST_HERMES_ROOT"]).resolve()
        self.assertTrue((root / "venv/bin/tirith").is_file(), "Preinstall the real Tirith scanner")
        await self.check_harness(
            "hermes",
            {
                "hermesRoot": str(root),
                "python": str(root / "venv/bin/python"),
                "nativeToolsets": ["terminal"],
                "env": {"TIRITH_BIN": str(root / "venv/bin/tirith")},
            },
        )

    @unittest.skipUnless(
        os.environ.get("RSI_TEST_DSH_ROOT"), "Set shared DSH CLI installation root"
    )
    async def test_dsh_shared_cli_tool_roundtrip(self) -> None:
        await self.check_harness("dsh", {})
