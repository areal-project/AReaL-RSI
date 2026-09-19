"""Mode changes must replace the frozen native session without losing intake."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from areal_rsi.base.storage import encode
from areal_rsi.coordination.brief_tool import PASSK_HIDDEN_LIMITS, brief_tool
from areal_rsi.coordination.config import ResearchConfig
from areal_rsi.coordination.engine import ResearchEngine
from areal_rsi.modules.research_runtime import ResearchRuntime
from areal_rsi.modules.runtime_package import RuntimeSettings
from areal_rsi.modules.runtime_tools import invoke
from areal_rsi.research_app import ResearchApp


@pytest.mark.parametrize("restart", [False, True])
def test_mode_changes_preserve_intake_with_fresh_native_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restart: bool
) -> None:
    monkeypatch.setenv("RSI_API_KEY", "fixture")
    monkeypatch.setenv(
        "RSI_RUNTIME_ISOLATION", "linux-unrestricted" if sys.platform == "linux" else "sandbox"
    )

    async def idle(self: ResearchEngine) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(ResearchEngine, "serve", idle)

    def runtime(config: ResearchConfig) -> ResearchRuntime:
        # The real worker, session persistence and tool bridge use the shipped
        # reference adapter, so this test needs no model service or DSH install.
        return ResearchRuntime(config, settings=RuntimeSettings({"default": {"kind": "fake"}}))

    async def scenario() -> None:
        app = ResearchApp(tmp_path, runtime_factory=runtime)
        try:
            detail = await app.create(
                {"conversation": True, "collaboration": {"mode": "passk", "modules": []}}
            )
            c = app.coordinator(detail["run_id"])
            sessions: list[Path] = []
            for index, modules in enumerate(((), ("leader_guidance",), ())):
                if index:
                    if restart:
                        await app.close()
                        app = ResearchApp(tmp_path, runtime_factory=runtime)
                    await app.set_collaboration(c, {"mode": "passk", "modules": list(modules)})
                    if restart:
                        await app.close()
                        app = ResearchApp(tmp_path, runtime_factory=runtime)
                active = await app.attach(app.coordinator(c.store.run_id))
                c = active.coordinator
                text = "Keep the dataset fixed" if index == 0 else "Continue the discussion"
                c.enqueue("user", "interaction", text, key=f"input-{index}")
                await asyncio.wait_for(active.engine._drive("interaction"), timeout=30)
                actor = c.actor("interaction")
                assert actor.error is None
                assert actor.session_id is not None
                session = c.store.paths.agent("interaction") / "sessions" / actor.session_id
                assert session not in sessions
                sessions.append(session)
                assert (session / "runtime/session-binding.json").exists()

                request = json.loads((session / "runtime/request.json").read_text())
                bridge = request["research_tools"]
                tool = next(tool for tool in bridge["tools"] if tool["name"] == "update_brief")
                assert tool["inputSchema"] == brief_tool("passk")["parameters"]
                arguments = encode(c.store.brief())
                arguments.update(objective="Preserve the task", instructions="Use fixed data")
                for name in PASSK_HIDDEN_LIMITS:
                    arguments["limits"].pop(name)
                result = await invoke(
                    bridge, {"name": "update_brief", "arguments": arguments, "call_id": "brief"}
                )
                assert "error" not in result
                assert c.store.brief().objective == "Preserve the task"

                if index:
                    interaction = active.engine.interaction_runtime
                    assert isinstance(interaction, ResearchRuntime)
                    message = interaction.conversations[session].session.turns[0]["content"]
                    assert "Keep the dataset fixed" in message
                    assert "fake runtime handled" in message  # Prior assistant reply.
                    assert (sessions[index - 1] / "runtime/session-binding.json").exists()
                    c.enqueue("user", "interaction", "One more detail", key=f"followup-{index}")
                    await asyncio.wait_for(active.engine._drive("interaction"), timeout=30)
                    assert c.actor("interaction").error is None
                    followup = interaction.conversations[session].session.turns[-2]["content"]
                    assert "backend_handoff" not in followup

                # Saving the same selection must not discard a compatible session.
                await app.set_collaboration(c, {"mode": "passk", "modules": list(modules)})
                assert c.actor("interaction").session_id == actor.session_id
        finally:
            await app.close()

    asyncio.run(scenario())
