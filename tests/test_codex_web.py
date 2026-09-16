"""Native Codex tool visibility, transparent search transport, and source lineage."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from areal_rsi.api.research import TaskBrief
from areal_rsi.base.storage import write_json
from areal_rsi.coordination.actions import definition
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.coordination.engine import ResearchEngine, ToolSet
from areal_rsi.modules.codex import NATIVE_REPLACEMENTS, CodexRuntime
from areal_rsi.modules.codex_transport import CodexGateway
from areal_rsi.modules.responses import ResponsesConfig, ToolCall, ToolResult
from areal_rsi.modules.runtime_research import ResearchRuntime
from areal_rsi.research_app import ResearchApp

Json = dict[str, Any]


@pytest.mark.parametrize("status", [200, 429])
def test_native_search_transports_original_protocol_and_archives_only_sources(
    tmp_path: Path, status: int
) -> None:
    async def scenario() -> None:
        c = Coordinator.create(tmp_path, TaskBrief("Research", "Use public documentation"))
        session = c.store.paths.agent("rsi") / "sessions" / "web"
        config = ResponsesConfig(base_url="https://example.test/proxy/v1", api_key="upstream-only")
        runtime = CodexRuntime(config)

        async def handler(call: ToolCall) -> ToolResult:
            raise AssertionError("Native search must not invoke project search tools")

        engine = ResearchEngine(c, runtime, lambda _: ToolSet([], handler))
        events: list[Json] = []
        forwarded: list[httpx.Request] = []
        reserved: list[str] = []
        payload = {
            "commands": {"open": [{"ref_id": "turn0search0"}]},
            "settings": {"external_web_access": True},
            "input": [{"text": "private-conversation-input"}],
        }
        body = {
            "output": "Retrieved original source text",
            "results": [{"url": "https://example.test/docs", "title": "Documentation"}],
            "encrypted_output": "private-continuation-state",
        }
        content = json.dumps(body).encode()

        async def respond(request: httpx.Request) -> httpx.Response:
            assert str(request.url) == "https://example.test/proxy/v1/alpha/search"
            assert request.headers["authorization"] == "Bearer upstream-only"
            assert json.loads(request.content) == payload
            forwarded.append(request)
            return httpx.Response(
                status, content=content, headers={"content-type": "application/json"}
            )

        async def emit(event: Json) -> None:
            events.append(event)
            await engine._event("rsi", session, event)

        gateway = CodexGateway(config, session, emit, reserved.append)
        await gateway.http.aclose()
        gateway.http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        await gateway.start()
        try:
            async with httpx.AsyncClient() as client:
                args: Json = {
                    "json": payload,
                    "headers": {"Authorization": "Bearer " + gateway.token},
                }
                response = await client.post(gateway.url + "/alpha/search", **args)
                assert response.status_code == status and response.content == content
                assert len(forwarded) == 1 and reserved == []
                assert c.state["model_calls"] == 0
                materials = list((c.store.root / "materials").glob("*.json"))
                assert len(materials) == 1
                source = json.loads(materials[0].read_text())
                assert source["source"] == "codex_web_search"
                assert source["text"] == body["output"] and source["results"] == body["results"]
                assert source["untrusted_source_content"] is True
                assert bool(source["limitations"]) == (status != 200)
                assert "private-" not in materials[0].read_text()
                await engine._event("rsi", session, events[-1])
                assert len([e for e in c.store.events() if e.kind == "source_read"]) == 1
                gateway.allow_web_access = False
                assert (await client.post(gateway.url + "/alpha/search", **args)).status_code == 400
                gateway.allow_web_access = True
                gateway.active = False
                assert (await client.post(gateway.url + "/alpha/search", **args)).status_code == 400
                assert len(forwarded) == 1
        finally:
            await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("allow_web", [True, False])
def test_pinned_native_runtime_advertises_web_and_never_subagents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, allow_web: bool
) -> None:
    """Run the actual bundled Codex against offline inference, including resume."""
    requests: list[Json] = []
    original = CodexGateway.__init__

    async def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/responses"
        requests.append(json.loads(request.content))
        message = {
            "id": "msg_fixture",
            "type": "message",
            "role": "assistant",
            "phase": "final_answer",
            "status": "completed",
            "content": [{"type": "output_text", "text": "Done", "annotations": []}],
        }
        events = [
            {"type": "response.output_item.done", "output_index": 0, "item": message},
            {
                "type": "response.completed",
                "response": {
                    "id": "resp_fixture",
                    "status": "completed",
                    "output": [message],
                    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                },
            },
        ]
        return httpx.Response(
            200,
            content="".join("data: " + json.dumps(e) + "\n\n" for e in events),
            headers={"content-type": "text/event-stream"},
        )

    def initialize(self: CodexGateway, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(respond))

    monkeypatch.setattr(CodexGateway, "__init__", initialize)

    async def scenario() -> None:
        runtime = CodexRuntime(
            ResponsesConfig(base_url="https://example.test/v1", api_key="fixture"),
            allow_web_access=allow_web,
        )
        assert runtime.for_interaction().allow_web_access == allow_web

        async def handler(call: ToolCall) -> ToolResult:
            raise AssertionError("No tools should be invoked by the offline model")

        ids = []
        try:
            for number in range(2):
                result = await asyncio.wait_for(
                    runtime.turn(
                        tmp_path,
                        system_prompt="Complete without tools.",
                        user_message="Done",
                        message_id=f"message-{number}",
                        tools=[
                            definition(name, name) for name in (*NATIVE_REPLACEMENTS, "get_state")
                        ],
                        handler=handler,
                    ),
                    timeout=30,
                )
                assert result.text == "Done"
                ids.append(result.session_id)
        finally:
            await runtime.close()
        assert ids[0] == ids[1] and len(requests) == 2
        for request in requests:
            tool_sets = [
                x["tools"] for x in request["input"] if x.get("type") == "additional_tools"
            ]
            assert tool_sets, "Inspect actual model-visible tools, not only requested config flags"
            encoded = json.dumps(tool_sets)
            assert ("web__run" in encoded) == allow_web
            assert "exec_command" in encoded and "apply_patch" in encoded
            assert "rsi__get_state" in encoded
            assert '"name": "collaboration"' not in encoded
            assert "spawn_agent" not in encoded and "create_goal" not in encoded
            for name in NATIVE_REPLACEMENTS:
                assert f'"name": "{name}"' not in encoded
                assert f"rsi__{name}" not in encoded

    asyncio.run(scenario())


@pytest.mark.parametrize("backend", ["standard", "codex"])
def test_app_keeps_backend_tools_and_instructions_when_execution_is_frozen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    monkeypatch.setenv("RSI_API_KEY", "fixture")

    async def idle(self: ResearchEngine) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(ResearchEngine, "serve", idle)

    async def scenario() -> None:
        app = ResearchApp(tmp_path)
        try:
            detail = await app.create({})
            c = app.coordinator(detail["run_id"])
            config = app.config(c)
            config.backend = backend
            config.metadata.update(
                allow_web_access=True, agent_tool_instructions="Operator constraint"
            )
            write_json(c.store.root / "coordination/config.json", config.public())
            active = await app.attach(c)
            for stage in ("attach", "freeze", "start"):
                if stage == "freeze":
                    await app.freeze_execution(c)
                if stage == "start":
                    c.state["intake"]["status"] = "ready"
                    c.save()
                    await app.start(c)
                names = {
                    tool["name"] for tool in active.actions.definitions("rsi", include_hidden=True)
                }
                assert ({"search_web", "fetch_url"} <= names) == (backend == "standard")
                prompt = active.tools.instructions("rsi")
                assert "Operator constraint" in prompt
                assert ("Codex 原生网页工具" in prompt) == (backend == "codex")
            config.metadata["allow_web_access"] = False
            app._configure_agent_tools(active, config)
            names = {
                tool["name"] for tool in active.actions.definitions("rsi", include_hidden=True)
            }
            assert not names & {"search_web", "fetch_url"}
            if backend == "codex":
                for runtime in (active.engine.runtime, active.engine.interaction_runtime):
                    assert isinstance(runtime, ResearchRuntime)
                    assert runtime.configuration()["web_search"] == "disabled"
        finally:
            await app.close()

    asyncio.run(scenario())
