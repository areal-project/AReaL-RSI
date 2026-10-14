"""Headless terminal interactions exercise the same run lifecycle as real input."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from runtime_fakes import CallbackRuntime
from test_goal import config, goal, wait_for
from textual.widgets import Input, RichLog, Static

from areal_rsi.api.interfaces import ToolCall, TurnResult
from areal_rsi.infra.coordination.tools.brief import brief_tool
from areal_rsi.infra.research import ResearchSystem
from areal_rsi.infra.storage.files import encode
from areal_rsi.ui.tui import ResearchTUI


def test_intake_handoff_leader_block_and_user_reply_in_tui(tmp_path: Path) -> None:
    async def scenario() -> None:
        leader_calls = 0

        async def respond(session: Path, delivery: dict[str, Any]) -> TurnResult:
            nonlocal leader_calls
            handler = delivery["handler"]
            if delivery["recovery_state"]["agent_id"] == "interaction":
                brief = encode(config().brief)
                fields = brief_tool("goal")["parameters"]["properties"]["limits"]["properties"]
                brief["limits"] = {k: v for k, v in brief["limits"].items() if k in fields}
                brief["limits"]["max_jobs"] = 2
                updated = await handler(ToolCall("update_brief", brief, "brief"))
                assert "error" not in updated.output
                result = await handler(ToolCall("start_research", {}, "start"))
                assert "error" not in result.output
            else:
                leader_calls += 1
                if leader_calls == 1:
                    result = await handler(
                        ToolCall("ask_user", {"question": "Which value?"}, "ask")
                    )
                else:
                    assert delivery["user_message"] == "42"
                    result = await handler(ToolCall("update_goal", goal(), "done"))
            return TurnResult(session.name, delivery["message_id"], "", result.reason, {})

        runtime = CallbackRuntime(respond)
        system = ResearchSystem(tmp_path, runtime_factory=lambda _: runtime)
        app = ResearchTUI(system, config())
        async with app.run_test(size=(110, 32)) as pilot:
            app.query_one(Input).value = "Write answer"
            await pilot.press("enter")
            await wait_for(lambda: app.run_id and system.coordinator(app.run_id).user_questions())
            assert app.run_id
            assert system.active[app.run_id].execution.manager.max_jobs == 2
            await app.poll()
            assert "Which value?" in str(app.query_one("#goal", Static).render())
            assert any("Which value?" in line.text for line in app.query_one(RichLog).lines)
            await pilot.press("ctrl+p")
            assert system.coordinator(app.run_id).state.status == "paused"
            app.query_one(Input).value = "/resume"
            await pilot.press("enter")
            app.query_one(Input).value = "42"
            await pilot.press("enter")
            await wait_for(lambda: system.coordinator(app.run_id).state.status == "succeeded")
            await app.poll()
            assert "succeeded" in str(app.query_one("#status", Static).render())
            assert not system.coordinator(app.run_id).store.run().research_agents
            app.query_one(Input).value = "/new"
            await pilot.press("enter")
            assert app.run_id is None
            await pilot.press("ctrl+q")
        assert app.closed and not system.active

    asyncio.run(scenario())
