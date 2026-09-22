"""Conversational startup requires a complete brief and survives clarification rounds."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from runtime_fakes import CallbackRuntime

from areal_rsi.api.interfaces import ToolCall, TurnResult
from areal_rsi.infra.coordination.tools.brief import PASSK_HIDDEN_LIMITS
from areal_rsi.infra.research import ResearchSystem
from areal_rsi.infra.storage.files import encode


@pytest.mark.parametrize("workspace", [False, True])
@pytest.mark.parametrize("requested_count", [None, 3])
def test_missing_acceptance_waits_then_hands_off_after_clarification_and_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace: bool, requested_count: int | None,
) -> None:
    expected_count = requested_count or 1
    monkeypatch.setenv("RSI_API_KEY", "fixture")
    if workspace:
        profiles = tmp_path / "development-profiles"
        profiles.mkdir()
        script = tmp_path / "prepare.py"
        script.write_text(
            "import json,sys,pathlib\n"
            "request=json.loads(pathlib.Path(sys.argv[1]).read_text())\n"
            "output=pathlib.Path(sys.argv[2])\n"
            "(output/'config.json').write_text(json.dumps(request['config']))\n"
            "(output/'checks.json').write_text(json.dumps([{'name':'Workspace',"
            "'status':'passed','message':'Ready'}]))\n"
        )
        (profiles / "project.json").write_text(json.dumps({
            "profile_id": "project", "title": "Project", "description": "",
            "defaults": {"profile_id": "project", "repository": str(tmp_path),
                         "revision": "main", "acceptance": "Old default", "parallelism": 4},
            "prepare_command": [sys.executable, str(script), "{request}", "{output}"],
        }))

    async def scenario() -> None:
        turns = 0
        researchers: list[str] = []

        async def respond(session: Path, delivery: dict[str, Any]) -> TurnResult:
            nonlocal turns
            actor = delivery["recovery_state"]["agent_id"]
            if actor != "interaction":
                researchers.append(actor)
                await asyncio.Event().wait()
                raise AssertionError("unreachable")
            turns += 1
            call = delivery["handler"]
            brief = encode(app.coordinator("intake").store.brief())
            for name in PASSK_HIDDEN_LIMITS:
                brief["limits"].pop(name)
            brief.update(objective="Fix login", instructions="Keep the public request format")
            brief["limits"]["max_agents"] = expected_count
            if turns == 2:
                brief["acceptance"] = [
                    "The empty-list regression test passes", "Request format unchanged"
                ]
            saved = await call(ToolCall("update_brief", brief, f"brief-{turns}"))
            assert "error" not in saved.output
            setup = await call(ToolCall("get_setup", {}, f"setup-{turns}"))
            assert app.coordinator("intake").store.brief().limits.max_agents == expected_count
            handoff = {"confirmation_message_id": delivery["message_id"],
                       "summary": f"Fix login; keep format; {expected_count} researchers",
                       "execution_required": False}
            if turns == 1:
                assert setup.output["missing_requirements"] == ["验收标准"]
                rejected = await call(ToolCall("handoff_research", handoff, "premature"))
                assert "验收标准" in rejected.output["error"]
                prepared = await call(ToolCall("prepare_research", {
                    "summary": "Fix login", "execution_required": False}, "premature-summary"))
                assert prepared.output["ready"] is False
                result = await call(ToolCall("ask_user",
                                            {"question": "怎样判断本次修复完成？"}, "ask"))
            else:
                assert setup.output["missing_requirements"] == []
                if workspace:
                    prepared = await call(ToolCall("prepare_workspace", {}, "workspace"))
                    assert prepared.output["status"] == "ready", prepared.output
                    preparation = app.coordinator("intake").state.preparation
                    assert preparation and preparation.setup.parallelism == expected_count
                    assert preparation.setup.acceptance == "\n".join(brief["acceptance"])
                result = await call(ToolCall("handoff_research", handoff, "handoff"))
                assert result.output["started"]
            return TurnResult(session.name, delivery["message_id"], "", result.reason, {})

        app = ResearchSystem(tmp_path, runtime_factory=lambda _: CallbackRuntime(respond))
        try:
            message = "帮我修复登录问题，保持接口格式不变。"
            if requested_count:
                message += f"请使用 {requested_count} 名 researcher。"
            await app.create({"run_id": "intake", "conversation": True,
                              "message": message,
                              "use_workspace": workspace})
            async with asyncio.timeout(5):
                while not (await app.detail("intake"))["waiting_for_user"]:
                    await asyncio.sleep(.01)
            c = app.coordinator("intake")
            assert c.state.status == "planned" and not c.store.run().research_agents
            assert turns == 1 and not researchers
            question = c.user_questions()[0].message_id
            await app.close()
            app = ResearchSystem(tmp_path, runtime_factory=lambda _: CallbackRuntime(respond))
            await app.send_message("intake", "空列表回归测试通过，接口格式保持不变，开始吧。",
                                   reply_to=question)
            async with asyncio.timeout(5):
                while not researchers:
                    await asyncio.sleep(.01)
            c = app.coordinator("intake")
            assert turns == 2
            assert len(c.store.run().research_agents) == expected_count
            assert c.store.brief().acceptance == [
                "The empty-list regression test passes", "Request format unchanged"
            ]
            assert c.store.brief().instructions == "Keep the public request format"
            assert c.store.brief().limits.max_wall_seconds is None
            assert c.state.intake.confirmation_message_id
            await app.stop("intake")
        finally:
            await app.close()

    asyncio.run(scenario())
