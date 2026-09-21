"""Tool identity follows the RSI delivery across native turns and process recovery."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
from pathlib import Path
from typing import Any

import pytest
from test_research_runtime_package import ResponsesModel

from areal_rsi.api.research import TaskBrief
from areal_rsi.infra.coordination.config import ResearchConfig
from areal_rsi.infra.coordination.coordinator import Coordinator
from areal_rsi.infra.coordination.tools.actions import ResearchActions
from areal_rsi.infra.runtime.research_runtime import ResearchRuntime
from areal_rsi.infra.runtime.runtime_package import PackageSession, RuntimeSettings
from areal_rsi.infra.runtime.runtime_tools import invoke


def test_effect_survives_process_restart_and_explicit_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RSI_API_KEY", "fixture")
    original = PackageSession.send
    calls: list[str] = []
    crash = True
    stop = False

    async def send(self: PackageSession, message: str, **kwargs: Any) -> str:
        nonlocal crash
        text = await original(self, message, **kwargs)
        bridge = json.loads((self.directory / "research-tools.json").read_text())
        calls.append(self.run_ids[-1])
        await invoke(bridge, {
            "name": "stop_run" if stop else "update_plan",
            "arguments": {"reason": "done"} if stop else {"plan": "inspect", "reason": "smoke"},
            "call_id": "reused-native-id",
        })
        if crash:
            crash = False
            raise RuntimeError("Crash after effect, before delivery receipt")
        return text

    monkeypatch.setattr(PackageSession, "send", send)

    async def scenario() -> None:
        nonlocal stop
        c = Coordinator.create(tmp_path, TaskBrief("identity", "smoke"))
        c.start()
        actions = ResearchActions(c)
        config = ResearchConfig(c.store.brief())
        settings = RuntimeSettings({"default": {"kind": "fake"}})
        runtime = ResearchRuntime(config, settings=settings)
        session = c.store.paths.agent("rsi") / "sessions/rsi"
        kwargs: dict[str, Any] = dict(
            system_prompt="smoke", tools=actions.definitions("rsi"),
            handler=actions.handler("rsi"), workspace=actions.workspace("rsi"),
        )
        try:
            with pytest.raises(RuntimeError, match="Crash after effect"):
                await runtime.turn(session, user_message="first", message_id="first", **kwargs)
            assert c.state.plan == "inspect"
            await runtime.close()
            runtime = ResearchRuntime(config, settings=settings)
            # A retry grant changes the receipt, but must not change existing effect keys.
            completed = await runtime.turn(
                session, user_message=None, message_id="first", retry_id="retry", **kwargs
            )
            assert len(set(calls)) == 2, "The recovery must actually create another native Run"
            assert len(list((c.store.root / "actions").glob("*.json"))) == 1
            assert sum(e.kind == "plan_updated" for e in c.store.events()) == 1
            replay = await runtime.turn(
                session, user_message=None, message_id="first", retry_id="retry", **kwargs
            )
            assert replay == completed and len(calls) == 2
            bridge = json.loads((session / "runtime/research-tools.json").read_text())
            with pytest.raises(ValueError, match="identity reused"):
                await invoke(bridge, {
                    "name": "update_plan", "arguments": {"plan": "changed", "reason": "smoke"},
                    "call_id": "reused-native-id",
                })
            assert c.state.plan == "inspect"
            stop = True
            finished = await runtime.turn(
                session, user_message="second", message_id="second", **kwargs
            )
            assert finished.stop_reason == "stopped" and c.state.status == "stopped"
            assert len(list((c.store.root / "actions").glob("*.json"))) == 2
        finally:
            await runtime.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("backend", [
    "codex",
    pytest.param("standard", marks=pytest.mark.skipif(
        os.environ.get("RSI_TEST_NATIVE_HARNESSES") != "1", reason="Opt in to native DSH"
    )),
])
def test_native_stop_run_after_another_turn_reuses_the_provider_call_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    monkeypatch.setenv("RSI_API_KEY", "fixture")
    model = ResponsesModel()

    def output(request: dict[str, Any]) -> dict[str, Any]:
        items = request["input"]
        user = max(i for i, item in enumerate(items) if item.get("role") == "user")
        if any(item.get("type") == "function_call_output" for item in items[user + 1:]):
            return {
                "id": "msg_fixture", "type": "message", "role": "assistant",
                "phase": "final_answer", "status": "completed",
                "content": [{"type": "output_text", "text": "Done", "annotations": []}],
            }
        second = "second-delivery" in json.dumps(items[user])
        name = "stop_run" if second else "update_plan"
        args = {"reason": "smoke complete"} if second else {"plan": "inspect", "reason": "smoke"}
        if backend == "codex":
            args = {"cmd": "rsi-tools call " + name + " " + shlex.quote(json.dumps(args))
                    + " same-native-id"}
            name = "exec_command"
        else:
            name = next(tool["name"] for tool in request["tools"] if tool["name"].endswith(name))
        return {
            "id": "fc_same", "type": "function_call", "call_id": "same-native-id",
            "name": name, "arguments": json.dumps(args), "status": "completed",
        }

    model.output = output

    async def scenario() -> None:
        c = Coordinator.create(tmp_path, TaskBrief("identity", "smoke"))
        c.start()
        actions = ResearchActions(c)
        config = ResearchConfig(c.store.brief(), backend=backend, base_url=model.url,
                                model_settings_pinned=True)
        runtime = ResearchRuntime(config)
        session = c.store.paths.agent("rsi") / "sessions/rsi"
        kwargs: dict[str, Any] = dict(system_prompt="Use the requested task tool then finish",
                      tools=actions.definitions("rsi"), handler=actions.handler("rsi"),
                      workspace=actions.workspace("rsi"))
        try:
            await asyncio.wait_for(runtime.turn(
                session, user_message="first-delivery: update_plan", message_id="first", **kwargs
            ), 60)
            assert c.state.plan == "inspect"
            # Keep the same live session: DSH generates colliding IDs at this boundary.
            final = await asyncio.wait_for(runtime.turn(
                session, user_message="second-delivery: stop_run", message_id="second", **kwargs
            ), 60)
            assert final.stop_reason == "stopped" and c.state.status == "stopped"
            records = [json.loads(p.read_text()) for p in (c.store.root / "actions").glob("*.json")]
            assert sorted(r["name"] for r in records) == ["stop_run", "update_plan"]
            assert all(r["status"] == "completed" for r in records)
            assert len(model.requests) == 4
        finally:
            await runtime.close()

    try:
        asyncio.run(scenario())
    finally:
        model.close()
