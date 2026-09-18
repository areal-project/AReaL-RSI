"""Exercise the shipped runtime adapters against local inference, without API spend."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from areal_rsi.api.research import TaskBrief
from areal_rsi.coordination.actions import definition
from areal_rsi.coordination.config import ResearchConfig
from areal_rsi.modules.agent_runtime import ToolCall, ToolResult
from areal_rsi.modules.research_runtime import ResearchRuntime
from areal_rsi.modules.runtime_package import RuntimeSettings
from areal_rsi.modules.runtime_storage import read_journal
from areal_rsi.modules.runtime_tools import invoke
from areal_rsi.research_app import ResearchApp


class ResponsesModel:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.force_tool = False
        self.tool_name = "rsi_mcp_probe"
        self.tool_arguments = {"expression": "6 * 7"}
        self.fail_requests = 0
        self.fail_after_tool = 0
        self.output: Callable[[dict[str, Any]], dict[str, Any]] | None = None
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append(request)
                if owner.fail_requests:
                    owner.fail_requests -= 1
                    self.send_response(503)
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(b'{"error":{"message":"temporarily unavailable"}}')
                    return
                owner.entered.set()
                owner.release.wait(20)
                if owner.fail_after_tool and any(
                    item.get("type") == "function_call_output" for item in request["input"]
                ):
                    owner.fail_after_tool -= 1
                    self.send_error(503, "Temporary fixture outage")
                    return
                message: dict[str, Any] = {
                    "id": "msg_fixture",
                    "type": "message",
                    "role": "assistant",
                    "phase": "final_answer",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "Done", "annotations": []}],
                }
                output = message
                if owner.force_tool and not any(
                    x.get("type") == "function_call_output" for x in request["input"]
                ):
                    tool = next(
                        t
                        for t in request["tools"]
                        if owner.tool_name in t["name"].replace(".", "_")
                    )
                    output = {
                        "id": "fc_probe",
                        "type": "function_call",
                        "call_id": "probe",
                        "name": tool["name"],
                        "arguments": json.dumps(owner.tool_arguments),
                        "status": "completed",
                    }
                if owner.output is not None:
                    output = owner.output(request)
                events: list[dict[str, Any]] = [
                    {
                        "type": "response.created",
                        "response": {
                            "id": "response_fixture",
                            "status": "in_progress",
                            "output": [],
                            "created_at": 1,
                        },
                    }
                ]
                if output["type"] == "function_call":
                    events += [
                        {
                            "type": "response.output_item.added",
                            "output_index": 0,
                            "item": {**output, "arguments": ""},
                        },
                        {
                            "type": "response.function_call_arguments.delta",
                            "output_index": 0,
                            "item_id": output["id"],
                            "delta": output["arguments"],
                        },
                        {
                            "type": "response.function_call_arguments.done",
                            "output_index": 0,
                            "item_id": output["id"],
                            "arguments": output["arguments"],
                        },
                    ]
                else:
                    events += [
                        {
                            "type": "response.output_item.added",
                            "output_index": 0,
                            "item": {**output, "content": []},
                        },
                        {
                            "type": "response.content_part.added",
                            "output_index": 0,
                            "content_index": 0,
                            "item_id": "msg_fixture",
                            "part": {"type": "output_text", "text": "", "annotations": []},
                        },
                        {
                            "type": "response.output_text.delta",
                            "output_index": 0,
                            "content_index": 0,
                            "item_id": "msg_fixture",
                            "delta": output["content"][0]["text"],
                        },
                        {
                            "type": "response.output_text.done",
                            "output_index": 0,
                            "content_index": 0,
                            "item_id": "msg_fixture",
                            "text": output["content"][0]["text"],
                        },
                    ]
                events += [
                    {"type": "response.output_item.done", "output_index": 0, "item": output},
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "response_fixture",
                            "status": "completed",
                            "output": [output],
                            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                        },
                    },
                ]
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    self.wfile.write(
                        "".join("data: " + json.dumps(e) + "\n\n" for e in events).encode()
                    )
                    self.wfile.flush()
                except BrokenPipeError:
                    pass
                self.close_connection = True

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"

    def close(self) -> None:
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def test_codex_native_tools_history_resume_and_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RSI_API_KEY", "fixture")
    model = ResponsesModel()

    async def scenario() -> None:
        config = ResearchConfig(
            TaskBrief("test", "test"),
            backend="codex",
            base_url=model.url,
            model="gpt-5.6-luna",
            model_settings_pinned=True,
            context_window_tokens=64000,
            metadata={"allow_web_access": True},
        )
        runtime = ResearchApp._runtime(config)
        assert isinstance(runtime, ResearchRuntime)
        events: list[dict[str, Any]] = []

        async def emit(event: dict[str, Any]) -> None:
            events.append(event)

        async def handle(call: ToolCall) -> ToolResult:
            return ToolResult({"private": "my-own-state"})

        session = tmp_path / "session"
        binding = None
        try:
            for i in range(2):
                result = await asyncio.wait_for(
                    runtime.turn(
                        session,
                        system_prompt="Complete without tools",
                        user_message=f"Remember turn {i}",
                        message_id=f"message-{i}",
                        tools=[definition("get_state", "Read your state")],
                        handler=handle,
                        workspace=tmp_path / "workspace",
                        event_sink=emit,
                        recovery_state={"brief": "recovery-only-task", "budget": {"remaining": 30}},
                    ),
                    30,
                )
                assert result.text == "Done"
                current = json.loads((session / "runtime/session-binding.json").read_text())
                native_config = (session / "runtime/codex-home/config.toml").read_text()
                assert "model_context_window = 64000" in native_config
                if binding:
                    assert current == binding
                binding = current
                assert runtime.has_session(session)
                if i == 0:
                    await runtime.close()  # Continue on the same native thread after restart.
            replay = await runtime.turn(
                session,
                system_prompt="Complete without tools",
                user_message="Remember turn 1",
                message_id="message-1",
                tools=[definition("get_state", "Read your state")],
                handler=handle,
                workspace=tmp_path / "workspace",
                event_sink=emit,
            )
            assert replay == result, "A committed delivery must not start another model turn"
            assert len(model.requests) == 2
            assert "Remember turn 0" in json.dumps(model.requests[-1]["input"])
            for i, request in enumerate(model.requests):
                inputs = json.dumps(request["input"])
                assert ("recovery-only-task" in inputs) == (i == 1)
                assert "remaining" in inputs
            for request in model.requests:
                tools = json.dumps(request.get("tools", [])) + json.dumps(
                    [item for item in request["input"] if item.get("type") == "additional_tools"]
                )
                assert "web__run" in tools or "web_search" in tools
                assert "exec_command" in tools and "apply_patch" in tools
                assert "spawn_agent" not in tools and "create_goal" not in tools
                assert request["reasoning"]["effort"] == "xhigh"
            assert any(e["type"] == "runtime_usage" for e in events)
            assert (session / "runtime/data/state/journal.jsonl").exists()
        finally:
            await runtime.close()

    try:
        asyncio.run(scenario())
    finally:
        model.close()


@pytest.mark.skipif(
    os.environ.get("RSI_TEST_NATIVE_HARNESSES") != "1", reason="Opt in to native DSH installation"
)
def test_standard_uses_package_dsh_and_scoped_application_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RSI_API_KEY", "fixture")
    model = ResponsesModel()
    model.force_tool = True

    async def scenario() -> None:
        config = ResearchConfig(
            TaskBrief("test", "test"),
            base_url=model.url,
            model="fixture",
            model_settings_pinned=True,
            context_window_tokens=96000,
        )
        runtime = ResearchApp._runtime(config)
        assert isinstance(runtime, ResearchRuntime)
        calls = []

        async def handle(call: ToolCall) -> ToolResult:
            calls.append(call)
            return ToolResult({"answer": 42})

        try:
            result = await asyncio.wait_for(
                runtime.turn(
                    tmp_path / "session",
                    system_prompt="Use the probe, then finish",
                    user_message="Probe",
                    tools=[definition("rsi_mcp_probe", "Probe", expression={"type": "string"})],
                    handler=handle,
                    workspace=tmp_path / "workspace",
                ),
                120,
            )
            assert result.text == "Done"
            assert len(calls) == 1 and calls[0].arguments == {"expression": "6 * 7"}
            assert result.usage["total_tokens"] > 0
            assert "42" in json.dumps(model.requests[-1]["input"])
            assert model.requests[0]["reasoning"]["effort"] == "xhigh"
            assert runtime.configuration()["harness"] == "dsh"
            patch = json.loads((tmp_path / "session/runtime/research-model.patch.json").read_text())
            assert (
                patch[0]["config"]["providers"]["rsi-responses"]["models"][0]["contextWindow"]
                == 96000
            )
        finally:
            await runtime.close()

    try:
        asyncio.run(scenario())
    finally:
        model.close()


@pytest.mark.skipif(os.uname().sysname != "Darwin", reason="macOS sandbox-exec policy")
def test_tool_capability_and_workspace_sandbox_survive_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RSI_API_KEY", "fixture")

    async def scenario() -> None:
        runtime = ResearchRuntime(
            ResearchConfig(TaskBrief("test", "test")),
            settings=RuntimeSettings({"default": {"kind": "fake"}}),
        )
        calls: list[str] = []

        async def handle(call: ToolCall) -> ToolResult:
            calls.append(call.name)
            return ToolResult("own-state")

        session = tmp_path / "session"
        try:
            await runtime.turn(
                session,
                system_prompt="test",
                user_message="test",
                tools=[definition("get_state", "State")],
                handler=handle,
                workspace=tmp_path / "workspace",
            )
            bridge = json.loads((session / "runtime/request.json").read_text())["research_tools"]
            process = await asyncio.create_subprocess_exec(
                "/usr/bin/sandbox-exec",
                "-f",
                str(session / "runtime/sandbox.sb"),
                str(session / "runtime/bin/rsi-tools"),
                "call",
                "get_state",
                "{}",
                "shell-call",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            assert process.returncode == 0, stderr.decode()
            assert json.loads(stdout) == "own-state"
            assert (
                await invoke(bridge, {"name": "get_state", "arguments": {}, "call_id": "one"})
                == "own-state"
            )
            with pytest.raises(ValueError, match="capability"):
                await invoke(
                    {**bridge, "token": "wrong"},
                    {"name": "get_state", "arguments": {}, "call_id": "two"},
                )
            with pytest.raises(ValueError, match="not available"):
                await invoke(
                    bridge, {"name": "spawn_researcher", "arguments": {}, "call_id": "three"}
                )
            assert calls == ["get_state", "get_state"]
            connection = runtime.conversations[session]
            pending = [{"message_id": "job-completed", "text": "job result"}]
            delivered: list[dict[str, Any]] = []

            async def wait_handler(call: ToolCall) -> ToolResult:
                return ToolResult("waiting", stop=True, reason="job")

            async def sink(event: dict[str, Any]) -> None:
                delivered.append(event)
                if event["type"] == "message_injected":
                    pending[:] = [x for x in pending if x["message_id"] != event["message_id"]]

            connection.sink = sink
            connection.incoming = lambda: pending
            output = await invoke(
                bridge, {"name": "get_state", "arguments": {}, "call_id": "receive"}
            )
            assert output["incoming_messages"] == [
                {"message_id": "job-completed", "text": "job result"}
            ]
            async with asyncio.timeout(2):
                while pending:
                    await asyncio.sleep(0.01)
            again = await invoke(
                bridge, {"name": "get_state", "arguments": {}, "call_id": "again"}
            )
            assert again == "own-state"
            assert sum(e["type"] == "message_injected" for e in delivered) == 1
            pending.append({"message_id": "late-job", "text": "late result"})
            delivered.clear()
            connection.handler = wait_handler
            output = await invoke(bridge, {"name": "get_state", "arguments": {}, "call_id": "wait"})
            assert output["turn_finished"] and "incoming_messages" not in output
            assert not any(e["type"] == "message_injected" for e in delivered)
            await runtime.close()
            await runtime.turn(
                session,
                system_prompt="test",
                user_message="continue",
                tools=[definition("get_state", "State")],
                handler=handle,
                workspace=tmp_path / "workspace",
            )
            connection = runtime.conversations[session]
            assert len(connection.session.run_ids) == 1  # This worker's live deliveries.
            events, _ = read_journal(session / "runtime/data/state")
            assert len({event.session_id for event in events}) == 1
            assert len({event.run_id for event in events}) == 2
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_disconnection_stops_package_worker_and_keeps_canonical_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RSI_API_KEY", "fixture")
    model = ResponsesModel()
    model.release.clear()

    async def scenario() -> None:
        runtime = ResearchRuntime(
            ResearchConfig(
                TaskBrief("test", "test"),
                backend="codex",
                base_url=model.url,
                model_settings_pinned=True,
            )
        )

        async def handle(call: ToolCall) -> ToolResult:
            return ToolResult("test")

        session = tmp_path / "session"
        task = asyncio.create_task(
            runtime.turn(
                session,
                system_prompt="test",
                user_message="wait",
                tools=[],
                handler=handle,
                workspace=tmp_path / "workspace",
            )
        )
        try:
            async with asyncio.timeout(30):
                while not model.entered.is_set():
                    if task.done():
                        await task
                    await asyncio.sleep(0.02)
            process = runtime.conversations[session].session.process
            assert process.stdin is not None
            process.stdin.close()  # Same EOF as losing the coordinator process.
            with pytest.raises(RuntimeError, match="worker exited"):
                await asyncio.wait_for(task, 15)
            assert process.returncode is not None
            events, _ = read_journal(session / "runtime/data/state")
            assert any(event.type == "run.cancelled" for event in events)
        finally:
            task.cancel()
            model.release.set()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.close()

    try:
        asyncio.run(scenario())
    finally:
        model.close()


def test_native_codex_passk_launches_independent_attempts_and_stops_on_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_passk import configuration

    from areal_rsi.api.research import VerifiedResult
    from areal_rsi.base.storage import now

    monkeypatch.setenv("RSI_API_KEY", "fixture")
    model = ResponsesModel()

    async def scenario() -> None:
        config = configuration(k=3, seconds=30)
        config.backend, config.base_url = "codex", model.url
        app = ResearchApp(tmp_path, config=config)
        try:
            created = await app.create({})
            await app.start(app.coordinator(created["run_id"]))
            c = app.coordinator(created["run_id"])
            active = app.active[c.store.run_id]
            # Model tool behavior is covered by the supervisor scheduler test;
            # this fixture answers text only, while all four native processes run.
            await active.actions.handler("rsi")(ToolCall("start_researchers", {}, "launch"))
            async with asyncio.timeout(25):
                while True:
                    agents = {actor.agent_id for actor in c.store.run().research_agents}
                    seen = {
                        agent
                        for request in model.requests
                        if "负责启动并监督" not in json.dumps(request["input"], ensure_ascii=False)
                        for agent in agents
                        if agent in json.dumps(request["input"])
                    }
                    if len(seen) == 3:
                        break
                    await asyncio.sleep(0.05)
            active = app.active[c.store.run_id]
            runtime = active.engine.runtime
            assert isinstance(runtime, ResearchRuntime)
            assert len(runtime.conversations) == 4
            processes = [item.session.process for item in runtime.conversations.values()]
            assert len({process.pid for process in processes}) == 4
            for request in model.requests:
                state = json.dumps(request["input"], ensure_ascii=False)
                if "负责启动并监督" in state:
                    continue
                assert sum(agent in state for agent in agents) == 1
            agent = sorted(agents)[0]
            candidate = await c.store.snapshot(
                active.actions.workspace(agent),
                agent_id=agent,
                description="Trusted verifier fixture",
            )
            result = VerifiedResult(
                "verified",
                candidate.candidate_id,
                c.store.contract_digest(),
                now(),
                "succeeded",
                passed=True,
                score=1.1,
                phase="search",
            )
            c.store.record_verification(result)
            c.store.consider_best(result.result_id)
            await asyncio.wait_for(active.task, 15)
            assert c.state["status"] == "succeeded"
            assert all(process.returncode is not None for process in processes)
            assert c.state["passk_target"]["result"]["candidate_id"] == candidate.candidate_id
            assert list(c.store.paths.agent("rsi").glob("sessions/*/runtime/request.json"))
            assert c.state["total_tokens"] > 0
        finally:
            await app.close()

    try:
        asyncio.run(scenario())
    finally:
        model.close()


@pytest.mark.skipif(
    os.environ.get("RSI_TEST_NATIVE_HARNESSES") != "1",
    reason="Opt in to native DSH installation",
)
def test_linux_dsh_executes_workspace_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    if sys.platform != "linux":
        pytest.skip("Linux unrestricted mode")
    monkeypatch.setenv("RSI_RUNTIME_ISOLATION", "linux-unrestricted")
    monkeypatch.setenv("RSI_API_KEY", "fixture")
    outside = tmp_path / "outside.txt"
    outside.write_text("LINUX_WORKSPACE_OK")
    model = ResponsesModel()
    model.force_tool = True
    model.tool_name = "workspace_run"
    model.tool_arguments = {"argv": ["/bin/bash", "-c", 'cat "$1"', "bash", str(outside)]}

    async def scenario() -> None:
        runtime = ResearchRuntime(
            ResearchConfig(
                TaskBrief("test", "test"),
                base_url=model.url,
                model="fixture",
                model_settings_pinned=True,
            )
        )

        async def handle(call: ToolCall) -> ToolResult:
            raise AssertionError("No application task tools expected")

        try:
            result = await asyncio.wait_for(
                runtime.turn(
                    tmp_path / "session",
                    system_prompt="Run the command then finish",
                    user_message="Test",
                    tools=[],
                    handler=handle,
                    workspace=tmp_path / "workspace",
                ),
                120,
            )
            assert result.text == "Done"
            assert "LINUX_WORKSPACE_OK" in json.dumps(model.requests[-1]["input"])
        finally:
            await runtime.close()

    try:
        asyncio.run(scenario())
    finally:
        model.close()
