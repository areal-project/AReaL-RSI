"""Backend contracts: asynchronous control, durable effects, and real-request budgets."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from areal_rsi.base.storage import write_json
from areal_rsi.coordination.config import config_from_dict, validate_model_settings
from areal_rsi.modules.agent_runtime import ToolCall, ToolResult, TurnResult
from areal_rsi.modules.codex import CodexRuntime, _CodexConversation
from areal_rsi.modules.codex_transport import AppServer, CodexGateway
from areal_rsi.modules.responses import ResponsesConfig, ResponsesError
from areal_rsi.research_app import ResearchApp

Json = dict[str, Any]


def test_codex_retries_transport_error_without_resetting_request_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = 0

    async def connected(self: _CodexConversation, *args: Any) -> TurnResult:
        nonlocal attempts
        attempts += 1
        self.reserve(f"attempt-{attempts}")
        if attempts == 1:
            self.state["thread_id"] = "same-thread"
            raise ResponsesError("Codex upstream transport error (ConnectError): ConnectError")
        assert self.state["thread_id"] == "same-thread"
        assert self.turn["requests"] == ["attempt-1", "attempt-2"]
        assert self.turn["request_limit"] == 2
        return await self.finish("Recovered")

    async def handler(call: ToolCall) -> ToolResult:
        raise AssertionError("No business tool should be replayed")

    monkeypatch.setattr(_CodexConversation, "connected", connected)
    result = asyncio.run(
        CodexRuntime(
            ResponsesConfig(
                base_url="https://example.test",
                api_key="fixture",
                max_model_calls_per_turn=2,
                retry_initial_seconds=0.001,
            )
        ).turn(tmp_path, system_prompt="test", user_message="work", tools=[], handler=handler)
    )
    assert attempts == 2 and result.session_id == "same-thread" and result.text == "Recovered"


@pytest.mark.parametrize("stream_started", [False, True])
def test_gateway_transport_failure_remains_retryable(tmp_path: Path, stream_started: bool) -> None:
    async def scenario() -> None:
        for name in ("requests", "responses"):
            (tmp_path / name).mkdir()

        async def emit(event: Json) -> None:
            pass

        class BrokenStream(httpx.AsyncByteStream):
            async def __aiter__(self) -> AsyncIterator[bytes]:
                yield b'data: {"type":"response.created"}\n\n'
                raise httpx.ReadError("")

        async def respond(request: httpx.Request) -> httpx.Response:
            if not stream_started:
                raise httpx.ConnectError("")
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=BrokenStream()
            )

        reserved: list[str] = []
        gateway = CodexGateway(
            ResponsesConfig(base_url="https://example.test", api_key="upstream-only"),
            tmp_path,
            emit,
            reserved.append,
        )
        await gateway.http.aclose()
        gateway.http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        await gateway.start()
        try:
            async with httpx.AsyncClient() as client:
                args: Json = {
                    "json": {"model": "gpt-6-astra", "input": []},
                    "headers": {"Authorization": "Bearer " + gateway.token},
                }
                if stream_started:
                    with pytest.raises(httpx.RemoteProtocolError):
                        await client.post(gateway.url + "/responses", **args)
                else:
                    response = await client.post(gateway.url + "/responses", **args)
                    assert response.status_code == 502
                    assert response.json()["error"] == {
                        "type": "transport_error",
                        "message": "ConnectError",
                    }
            assert isinstance(gateway.error, ResponsesError)
            assert "transport" in str(gateway.error)
            assert len(reserved) == 1
            record = json.loads(
                (tmp_path / "responses" / (reserved[0] + ".error.json")).read_text()
            )
            assert record["error_type"] == ("ReadError" if stream_started else "ConnectError")
            assert record["retryable"] is True
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_backend_defaults_and_validation() -> None:
    config = config_from_dict({"brief": {"objective": "test", "instructions": ""}})
    assert config.backend == "standard"
    settings = {
        "base_url": "https://example.test/v1",
        "model": "gpt-6-astra",
        "reasoning_effort": "xhigh",
    }
    assert validate_model_settings(settings)["backend"] == "standard"
    assert validate_model_settings({**settings, "backend": "codex"})["backend"] == "codex"
    with pytest.raises(ValueError, match="standard"):
        config_from_dict({"brief": {"objective": "test", "instructions": ""}, "backend": "typo"})


@pytest.mark.parametrize("backend", ["standard", "codex"])
def test_selected_backend_applies_to_interaction(
    monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    monkeypatch.setenv("RSI_API_KEY", "fixture-secret")
    config = config_from_dict(
        {"brief": {"objective": "test", "instructions": ""}, "backend": backend}
    )
    runtime = ResearchApp._runtime(config)
    assert runtime.backend == runtime.for_interaction().backend == backend
    for selected in (runtime, runtime.for_interaction()):
        assert selected.configuration()["framework"] == "areal-agent-runtime"


def test_backend_switch_preserves_intake_and_locks_after_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RSI_API_KEY", "fixture-secret")

    async def scenario() -> None:
        app = ResearchApp(tmp_path)
        try:
            detail = await app.create({})
            c = app.coordinator(detail["run_id"])
            previous = c.actor("interaction").session_id
            c.enqueue("user", "interaction", "Keep the previously agreed constraints", key="input")
            settings = app.config(c).model_settings()
            await app.set_model_settings(c, {**settings, "backend": "codex"})
            c = app.coordinator(c.store.run_id)
            actor = c.actor("interaction")
            assert actor.session_id and actor.session_id != previous
            handoff = c.store.paths.agent("interaction") / "sessions" / actor.session_id
            assert "previously agreed" in (handoff / "backend-handoff.json").read_text()
            assert app.config(c).backend == "codex"
            # Older clients changing model settings must not reset the backend.
            await app.set_model_settings(c, {k: v for k, v in settings.items() if k != "backend"})
            assert app.config(c).backend == "codex"
            c.state["intake"]["status"] = "ready"
            c.save()
            c.start()
            with pytest.raises(ValueError, match="已锁定"):
                await app.set_model_settings(c, {**settings, "backend": "standard"})
        finally:
            await app.close()

    asyncio.run(scenario())


def test_app_server_reads_interrupt_while_tool_is_waiting(tmp_path: Path) -> None:
    async def scenario() -> None:
        # A miniature peer initiates a tool call, then ACKs an interrupt before
        # receiving that tool's result. Serial callback dispatch deadlocks here.
        script = tmp_path / "peer.py"
        script.write_text("""import json,sys
def send(value): print(json.dumps(value),flush=True)
send({"id":"tool-1","method":"item/tool/call","params":{}})
for line in sys.stdin:
    value=json.loads(line)
    if value.get("method")=="turn/interrupt":
        send({"id":value["id"],"result":{"accepted":True}})
    elif value.get("id")=="tool-1":
        send({"method":"probe/completed","params":value["result"]})
""")

        async def handler(method: str, params: Json) -> Json:
            assert method == "item/tool/call"
            return await server.request("turn/interrupt", {})

        server = AppServer(tmp_path, {}, handler)
        server.process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(script),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        server.reader = asyncio.create_task(server._read())
        try:
            event = await asyncio.wait_for(server.events.get(), 3)
            assert isinstance(event, dict)
            assert event["params"] == {"accepted": True}
        finally:
            await server.close()
        assert not server.handlers

    asyncio.run(scenario())


def test_gateway_accounts_before_forwarding_and_archives_usage(tmp_path: Path) -> None:
    async def scenario() -> None:
        for name in ("requests", "responses"):
            (tmp_path / name).mkdir()
        events: list[Json] = []
        upstream: list[httpx.Request] = []
        reserved: list[str] = []
        config = ResponsesConfig(base_url="https://example.test", api_key="upstream-only")

        async def emit(event: Json) -> None:
            events.append(event)
            if event["type"] == "response_usage":
                assert (tmp_path / "responses" / (event["request_id"] + ".json")).exists()

        async def respond(request: httpx.Request) -> httpx.Response:
            assert events[-1]["type"] == "model_attempt"
            assert request.headers["authorization"] == "Bearer upstream-only"
            upstream.append(request)
            body = {
                "type": "response.completed",
                "response": {"usage": {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}},
            }
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=("data: " + json.dumps(body) + "\n\n").encode(),
            )

        gateway = CodexGateway(config, tmp_path, emit, reserved.append)
        await gateway.http.aclose()
        gateway.http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        await gateway.start()
        try:
            async with httpx.AsyncClient() as client:
                args: Json = {
                    "json": {"model": config.model, "input": []},
                    "headers": {"Authorization": "Bearer " + gateway.token},
                }
                response = await client.post(gateway.url + "/responses", **args)
                assert response.status_code == 200
                assert len(upstream) == len(reserved) == 1
                assert events[-1]["usage"]["total_tokens"] == 10
                gateway.active = False
                assert (await client.post(gateway.url + "/responses", **args)).status_code == 400
                assert len(upstream) == len(reserved) == 1
                assert "upstream-only" not in response.text
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_gateway_budget_rejection_never_reaches_provider(tmp_path: Path) -> None:
    async def scenario() -> None:
        (tmp_path / "requests").mkdir()

        async def emit(event: Json) -> None:
            raise ResponsesError("budget refused")

        gateway = CodexGateway(
            ResponsesConfig(base_url="https://invalid.test", api_key="secret"),
            tmp_path,
            emit,
            lambda _: None,
        )
        await gateway.start()
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    gateway.url + "/responses",
                    json={"input": []},
                    headers={"Authorization": "Bearer " + gateway.token},
                )
            assert response.status_code == 400
            assert "budget refused" in response.text
            assert gateway.error is not None
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_stop_receipt_survives_crash_and_does_not_restart_codex(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = CodexRuntime(ResponsesConfig(base_url="https://example.test", api_key="secret"))
        write_json(
            tmp_path / "codex-session.json",
            {
                "schema_version": 1,
                "backend": "codex",
                "thread_id": "same-thread",
                "turns": {
                    "assignment": {
                        "message_id": "assignment",
                        "user_message": "work",
                        "requests": [],
                        "status": "running",
                    }
                },
                "calls": {
                    "report-call": {
                        "name": "submit_report",
                        "arguments": {},
                        "message_id": "assignment",
                        "status": "running",
                    }
                },
                "injections": {},
            },
        )
        effects: list[str] = []

        async def handler(call: ToolCall) -> ToolResult:
            assert call.recovered
            # Domain handlers reconcile this ID with their already committed receipt.
            effects.append(call.call_id)
            return ToolResult({"report_id": "existing-report"}, stop=True, reason="rsi_reply")

        for _ in range(2):
            result = await runtime.turn(
                tmp_path,
                system_prompt="test",
                user_message=None,
                message_id="assignment",
                tools=[],
                handler=handler,
            )
            assert result.stop_reason == "rsi_reply"
            assert result.session_id == "same-thread"
        assert effects == ["report-call"]
        assert not runtime.connections

    asyncio.run(scenario())


def test_inbox_ack_is_durable_and_completion_race_retains_message(tmp_path: Path) -> None:
    async def scenario() -> None:
        events: list[Json] = []

        async def emit(event: Json) -> None:
            events.append(event)

        c = _CodexConversation(
            CodexRuntime(ResponsesConfig(base_url="https://example.test", api_key="secret")),
            tmp_path,
            emit,
        )
        c.state["thread_id"] = "thread"
        c.turn = {"message_id": "assignment"}
        c.rpc_turn_id = "turn"
        c.messages = lambda: [{"message_id": "update", "sender": "rsi", "text": "new fact"}]

        class Peer:
            fail = True
            calls = 0

            async def request(self, method: str, params: Json) -> Json:
                self.calls += 1
                assert method == "turn/steer" and params["expectedTurnId"] == "turn"
                if self.fail:
                    raise ResponsesError("no active turn")
                return {}

        peer: Any = Peer()
        c.server = peer
        await c.inject()
        assert not events and not c.state["injections"]["update"]["accepted"]
        peer.fail = False
        await c.inject()
        saved = json.loads(c.path.read_text())
        assert not saved["injections"]["update"]["accepted"]
        assert not events  # The RPC ACK alone is not a durable delivery.
        await c.inject()
        assert peer.calls == 2  # Do not enqueue another steer in this active turn.
        await c.acknowledge(
            {
                "type": "userMessage",
                "id": "native-item-id",
                "clientId": saved["injections"]["update"]["client_id"],
            }
        )
        assert json.loads(c.path.read_text())["injections"]["update"]["accepted"]
        assert events[-1]["message_id"] == "update"
        await c.inject()
        assert peer.calls == 2  # No second steer after a committed acknowledgement.

    asyncio.run(scenario())


def test_interrupted_steer_is_carried_into_next_wake(tmp_path: Path) -> None:
    async def scenario() -> None:
        events: list[Json] = []

        async def emit(event: Json) -> None:
            events.append(event)

        c = _CodexConversation(
            CodexRuntime(ResponsesConfig(base_url="https://example.test", api_key="secret")),
            tmp_path,
            emit,
        )
        c.state["injections"]["update"] = {
            "accepted": False,
            "client_id": "old-steer",
            "message_id": "old-assignment",
            "queued_turn_id": "interrupted-turn",
            "text": json.dumps({"message_id": "update", "sender": "rsi", "text": "SKY123"}),
        }
        c.save()
        # A new process has only the journal, with no in-memory inbox callback.
        c = _CodexConversation(c.runtime, tmp_path, emit)
        c.turn = {"message_id": "next-wake"}
        text = c.start_input("Continue", "new-client-id")
        assert "SKY123" in text and not events
        assert not c.state["injections"]["update"]["accepted"]
        await c.acknowledge(
            {"type": "userMessage", "id": "different-native-id", "clientId": "new-client-id"}
        )
        assert events == [{"type": "message_injected", "message_id": "update"}]
        assert "SKY123" not in c.start_input("Next task", "later-client-id")

    asyncio.run(scenario())


def test_completed_native_turn_recovered_before_start_ack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        methods: list[str] = []
        config = ResponsesConfig(base_url="https://example.test", api_key="secret")

        class Peer:
            def __init__(self, *args: Any):
                pass

            async def start(self) -> None:
                pass

            async def close(self) -> None:
                pass

            async def request(self, method: str, params: Json) -> Json:
                methods.append(method)
                assert method == "thread/resume"
                return {
                    "model": config.model,
                    "reasoningEffort": config.reasoning_effort,
                    "thread": {
                        "id": "thread",
                        "turns": [
                            {
                                "id": "native-turn",
                                "status": "completed",
                                "items": [
                                    {
                                        "type": "userMessage",
                                        "id": "native-item",
                                        "clientId": "persisted-before-start",
                                    },
                                    {
                                        "type": "agentMessage",
                                        "id": "reply",
                                        "text": "Already done",
                                        "phase": "final_answer",
                                    },
                                ],
                            }
                        ],
                    },
                }

        monkeypatch.setattr("areal_rsi.modules.codex.AppServer", Peer)
        write_json(
            tmp_path / "codex-session.json",
            {
                "schema_version": 1,
                "backend": "codex",
                "thread_id": "thread",
                "turns": {
                    "message": {
                        "message_id": "message",
                        "user_message": "work",
                        "requests": [],
                        "start_client_id": "persisted-before-start",
                        "status": "running",
                    }
                },
                "calls": {},
                "injections": {},
            },
        )

        async def handler(call: ToolCall) -> ToolResult:
            raise AssertionError("No tool should be replayed")

        result = await CodexRuntime(config).turn(
            tmp_path,
            system_prompt="test",
            user_message=None,
            message_id="message",
            tools=[],
            handler=handler,
        )
        assert result.text == "Already done" and result.stop_reason == "completed"
        assert methods == ["thread/resume"]

    asyncio.run(scenario())
