"""Per-run connection settings, start locking and live Interaction migration."""

from __future__ import annotations

import asyncio
import copy
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from areal_rsi.api.research import TaskBrief
from areal_rsi.base.storage import encode
from areal_rsi.coordination.config import MODEL_EFFORTS, ResearchConfig, validate_model_settings
from areal_rsi.modules.responses import ResponsesRuntime
from areal_rsi.research_app import ResearchApp
from areal_rsi.ui.workbench import WorkbenchServer

SETTINGS = {
    "base_url": "https://second.example/v1",
    "model": "gpt-5.6-terra",
    "reasoning_effort": "high",
}


def response(*output: dict[str, Any]) -> dict[str, Any]:
    return {"status": "completed", "output": list(output), "usage": {"total_tokens": 10}}


class WaitingTransport:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, Any]]] = []

    async def request(self, config: Any, payload: Any, sink: Any) -> dict[str, Any]:
        self.requests.append((config.endpoint, copy.deepcopy(payload)))
        return response(
            {
                "type": "function_call",
                "name": "wait",
                "call_id": str(len(self.requests)),
                "arguments": '{"reason":"等待后续消息"}',
            }
        )


async def idle(app: ResearchApp, run_id: str) -> None:
    async with asyncio.timeout(5):
        while app.coordinator(run_id).pending() or app.active[run_id].engine.active_agents:
            await asyncio.sleep(0.01)


def test_settings_are_independent_persisted_and_locked_after_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RSI_API_KEY", "test-only-secret")
    monkeypatch.setenv("RSI_BASE_URL", "https://initial.example")
    monkeypatch.setenv("RSI_MODEL", "gpt-6-astra")
    monkeypatch.setenv("RSI_REASONING_EFFORT", "xhigh")

    async def scenario() -> None:
        transport = WaitingTransport()
        app = ResearchApp(
            tmp_path,
            config=ResearchConfig(TaskBrief("研究", "推进目标")),
            runtime_factory=lambda cfg: ResponsesRuntime(cfg.runtime(), transport),
        )
        first, second = [await app.create({}) for _ in range(2)]
        run_id = first["run_id"]
        try:
            await app.call("model-settings", {"run_id": run_id, **SETTINGS})
            monkeypatch.setenv("RSI_MODEL", "gpt-5.6-luna")
            monkeypatch.setenv("RSI_BASE_URL", "https://changed-global.example")
            observer = ResearchApp(tmp_path)
            assert (await observer.detail(run_id))["model_settings"] == {
                **SETTINGS,
                "editable": True,
                "has_credentials": True,
            }
            assert (await observer.detail(second["run_id"]))["model_settings"] == second[
                "model_settings"
            ]
            await app.call("start", {"run_id": run_id})
            await idle(app, run_id)
            endpoint, request = transport.requests[0]
            assert endpoint == "https://second.example/v1/responses"
            assert request["model"] == "gpt-5.6-terra"
            assert request["reasoning"] == {"effort": "high"}
            for control in (None, "pause", "stop"):
                if control:
                    await app.call(control, {"run_id": run_id})
                assert not (await app.detail(run_id))["model_settings"]["editable"]
                with pytest.raises(ValueError, match="锁定"):
                    await app.call("model-settings", {"run_id": run_id, **SETTINGS})
            saved = app.coordinator(run_id).store.root / "coordination/started-config.json"
            assert json.loads(saved.read_text())["model"] == "gpt-5.6-terra"
            assert "test-only-secret" not in saved.read_text()
        finally:
            await app.close()

    asyncio.run(scenario())


def test_changing_model_during_request_preserves_conversation_and_completed_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RSI_API_KEY", "test")
    for name in ("RSI_BASE_URL", "RSI_MODEL", "RSI_REASONING_EFFORT"):
        monkeypatch.delenv(name, raising=False)

    async def scenario() -> None:
        waiting = asyncio.Event()

        class Transport(WaitingTransport):
            async def request(self, config: Any, payload: Any, sink: Any) -> dict[str, Any]:
                self.requests.append((config.endpoint, copy.deepcopy(payload)))
                if len(self.requests) == 1:
                    return response(
                        {
                            "type": "reasoning",
                            "id": "old-reasoning",
                            "encrypted_content": "private-old",
                        },
                        {
                            "type": "function_call",
                            "call_id": "update-once",
                            "name": "update_brief",
                            "arguments": json.dumps(
                                {
                                    "brief_json": json.dumps(
                                        encode(TaskBrief("已确认目标", "保留已讨论需求"))
                                    )
                                }
                            ),
                        },
                    )
                if config.model == "gpt-6-astra":
                    waiting.set()
                    await asyncio.Event().wait()
                assert payload["model"] == SETTINGS["model"]
                assert payload["reasoning"] == {"effort": "high"}
                assert "private-old" not in json.dumps(payload["input"])
                assert any(item.get("call_id") == "update-once" for item in payload["input"])
                return response(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "已保留需求，继续讨论。"}],
                    }
                )

        transport = Transport()
        app = ResearchApp(
            tmp_path, runtime_factory=lambda cfg: ResponsesRuntime(cfg.runtime(), transport)
        )
        run_id = (await app.create({}))["run_id"]
        try:
            await app.call(
                "chat", {"run_id": run_id, "text": "先确认需求", "message_id": "user-one"}
            )
            await asyncio.wait_for(waiting.wait(), 5)
            c = app.coordinator(run_id)
            session_id = c.actor("interaction").session_id
            session = (
                c.store.paths.agent("interaction") / "sessions" / str(session_id) / "session.json"
            )
            before = json.loads(session.read_text())
            await app.call("model-settings", {"run_id": run_id, **SETTINGS})
            await idle(app, run_id)
            after = json.loads(session.read_text())
            assert c.actor("interaction").session_id == session_id
            assert after["history"][: len(before["history"])] == before["history"]
            assert after["model"] == SETTINGS["model"]
            assert len(after["turns"]) == 1
            assert after["calls"]["update-once"]["status"] == "completed"
            assert sum(e.kind == "brief_updated" for e in c.store.events()) == 1
            assert c.store.brief().instructions == "保留已讨论需求"
            assert [payload["model"] for _, payload in transport.requests] == [
                "gpt-6-astra",
                "gpt-6-astra",
                "gpt-5.6-terra",
            ]
            assert (await app.detail(run_id))["messages"][-1]["text"] == "已保留需求，继续讨论。"
        finally:
            await app.close()

    asyncio.run(scenario())


def test_settings_waiting_for_start_cannot_overwrite_started_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RSI_API_KEY", "test")

    async def scenario() -> None:
        app = ResearchApp(
            tmp_path,
            config=ResearchConfig(TaskBrief("研究", "推进目标")),
            runtime_factory=lambda cfg: ResponsesRuntime(cfg.runtime(), WaitingTransport()),
        )
        run_id = (await app.create({}))["run_id"]
        before = (await app.detail(run_id))["model_settings"]
        # Queue two HTTP-equivalent actions against separate, initially planned
        # Coordinator objects. The later save must read the post-start state.
        lock = app.run_lock(app.coordinator(run_id))
        await lock.acquire()
        starting = asyncio.create_task(app.call("start", {"run_id": run_id}))
        changing = asyncio.create_task(app.call("model-settings", {"run_id": run_id, **SETTINGS}))
        try:
            await asyncio.sleep(0)
            lock.release()
            await starting
            with pytest.raises(ValueError, match="锁定"):
                await changing
            assert (await app.detail(run_id))["model_settings"] == {
                **before,
                "editable": False,
            }
        finally:
            await app.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "changes",
    [
        {"base_url": "file:///tmp/model"},
        {"base_url": "https://secret@example.com"},
        {"base_url": "https://example.com?key=secret"},
        {"base_url": "https://exa mple.com"},
        {"model": "invalid model"},
        {"model": "gpt-6-astra", "reasoning_effort": "none"},
        {"reasoning_effort": "unsupported"},
    ],
)
def test_invalid_settings_are_rejected(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        validate_model_settings({**SETTINGS, **changes})


def test_model_settings_http_contract_and_projection(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("RSI_API_KEY", raising=False)
    app = ResearchApp(tmp_path)
    server = WorkbenchServer(app, port=0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    def request(path: str, body: Any = None, token: str = "") -> Any:
        data = json.dumps(body).encode() if body is not None else None
        with urllib.request.urlopen(
            urllib.request.Request(
                server.url + path,
                data=data,
                headers={"Content-Type": "application/json", "X-RSI-Token": token},
            )
        ) as result:
            return json.load(result)

    try:
        bootstrap = request("/api/bootstrap")
        token = bootstrap["csrf_token"]
        assert {model["id"] for model in bootstrap["models"]} == set(MODEL_EFFORTS)
        run_id = request("/api/runs", {"message": ""}, token)["run_id"]
        with pytest.raises(urllib.error.HTTPError) as exc:
            request(f"/api/runs/{run_id}/model-settings", SETTINGS)
        assert exc.value.code == 403
        request(f"/api/runs/{run_id}/model-settings", SETTINGS, token)
        view = request(f"/api/runs/{run_id}")
        assert view["model_settings"] == {**SETTINGS, "editable": True, "has_credentials": False}
        assert "config" not in view and "api_key" not in json.dumps(view)
        assert view["budget"]["model_calls"] == 0
    finally:
        server.shutdown()
        asyncio.run_coroutine_threadsafe(app.close(), server.loop).result(5)
        server.server_close()
        worker.join(5)


def test_new_run_credentials_are_private_persisted_and_used_on_first_call(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_API_KEY", "default-test-secret")

    async def scenario():
        seen = []

        class Transport(WaitingTransport):
            async def request(self, config, payload, sink):
                seen.append((config.model, config.api_key))
                return response(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "请描述研究目标。"}],
                    }
                )

        app = ResearchApp(
            tmp_path, runtime_factory=lambda cfg: ResponsesRuntime(cfg.runtime(), Transport())
        )
        try:
            first = await app.create(
                {
                    "model_settings": {
                        **SETTINGS,
                        "model": "custom-model",
                        "api_key": "run-test-secret",
                    },
                    "message": "开始讨论",
                }
            )
            second = await app.create({})
            await idle(app, first["run_id"])
            assert seen == [("custom-model", "run-test-secret")]
            observer = ResearchApp(tmp_path)
            c = observer.coordinator(first["run_id"])
            assert observer.config(c).runtime().api_key == "run-test-secret"
            assert (
                observer.config(observer.coordinator(second["run_id"])).runtime().api_key
                == "default-test-secret"
            )
            secret = c.store.root / "coordination/credentials.json"
            assert secret.stat().st_mode & 0o777 == 0o600
            assert "run-test-secret" not in json.dumps(await observer.detail(first["run_id"]))
            assert "run-test-secret" not in (c.store.root / "coordination/config.json").read_text()
            await app.call(
                "model-settings",
                {"run_id": first["run_id"], **SETTINGS, "api_key": "updated-test-secret"},
            )
            assert (
                observer.config(observer.coordinator(first["run_id"])).runtime().api_key
                == "updated-test-secret"
            )
            await app.call("model-settings", {"run_id": first["run_id"], **SETTINGS, "api_key": ""})
            assert (
                observer.config(observer.coordinator(first["run_id"])).runtime().api_key
                == "updated-test-secret"
            )
        finally:
            await app.close()

    asyncio.run(scenario())
