"""Role-scoped DSH tool wiring and upgrades of existing conversations."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from areal_rsi.modules.dsh_tools import definitions


def test_shell_output_reaches_model_once_without_losing_execution_details() -> None:
    from areal_rsi.coordination.agent_tools import model_output

    original = {
        "isError": False,
        "content": [{"type": "text", "text": "partial output\nwarning"}],
        "value": {
            "exitCode": 2,
            "timedOut": False,
            "stdout": {"text": "partial output", "truncated": True, "spillPath": "/tmp/full"},
            "stderr": {"text": "warning", "truncated": False},
        },
    }
    projected = model_output("bash", original)
    assert "content" not in projected
    assert projected["value"] == original["value"]
    assert projected["isError"] is False
    assert "content" in original
    assert model_output("read", original) is original
    error = {"isError": True, "content": [{"type": "text", "text": "OUTCOME_UNKNOWN"}]}
    assert model_output("bash", error) is error


@pytest.fixture
def installation() -> Path:
    configured = os.environ.get("RSI_DSH_TOOLS_TEST_INSTALLATION")
    if not configured or not shutil.which("node"):
        pytest.skip("Set RSI_DSH_TOOLS_TEST_INSTALLATION to run real DSH integration")
    return Path(configured).resolve()


def test_all_roles_have_basic_tools_but_keep_their_duties(tmp_path: Path) -> None:
    from areal_rsi.api.research import TaskBrief
    from areal_rsi.coordination.actions import ResearchActions
    from areal_rsi.coordination.agent_tools import AgentTools
    from areal_rsi.coordination.coordinator import Coordinator
    from areal_rsi.coordination.sources import register_sources

    actions = ResearchActions(Coordinator.create(tmp_path, TaskBrief("Tools", "Check roles")))
    AgentTools(actions, tmp_path, environment=dict, credential_names=())
    register_sources(actions)
    by_role = {
        role: {tool["name"] for tool in actions.definitions(role)}
        for role in ("interaction", "rsi", "researcher_1")
    }
    for names in by_role.values():
        assert {"bash", "read", "write", "edit", "glob", "grep", "search_web", "fetch_url"} <= names
    assert "spawn_researcher" in by_role["rsi"]
    assert "spawn_researcher" not in by_role["interaction"] | by_role["researcher_1"]
    assert "submit_report" in by_role["researcher_1"]
    assert "submit_report" not in by_role["interaction"] | by_role["rsi"]


def test_existing_interaction_conversation_resumes_with_new_tools(
    installation: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import copy

    from areal_rsi.api.research import TaskBrief
    from areal_rsi.coordination import agent_tools
    from areal_rsi.coordination.config import ResearchConfig
    from areal_rsi.modules.responses import ResponsesConfig, ResponsesRuntime
    from areal_rsi.research_app import ResearchApp

    class Transport:
        def __init__(self) -> None:
            self.payloads: list[dict[str, Any]] = []

        async def request(self, config: Any, payload: Any, sink: Any) -> dict[str, Any]:
            self.payloads.append(copy.deepcopy(payload))
            index = len(self.payloads)
            output: list[dict[str, Any]]
            if index == 2:
                output = [
                    {
                        "type": "function_call",
                        "name": "bash",
                        "call_id": "new-bash",
                        "arguments": json.dumps(
                            {
                                "command": "printf upgraded",
                                "description": "检查新增工具",
                            }
                        ),
                    }
                ]
            else:
                output = [
                    {
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {"type": "output_text", "text": "原有回复" if index == 1 else "已检查"}
                        ],
                    }
                ]
            return {
                "id": f"response_{index}",
                "status": "completed",
                "output": output,
                "usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30},
            }

    async def settle(app: ResearchApp, run_id: str, calls: int, transport: Transport) -> None:
        for _ in range(300):
            if len(transport.payloads) == calls and not app.active[run_id].engine.active_agents:
                return
            await asyncio.sleep(0.01)
        raise AssertionError("Agent did not finish its expected turn")

    async def scenario() -> None:
        transport = Transport()
        config = ResearchConfig(TaskBrief("研究现有任务", "保留所有要求"))

        def runtime(_: ResearchConfig) -> ResponsesRuntime:
            return ResponsesRuntime(
                ResponsesConfig(base_url="https://example.test", api_key="fixture"), transport
            )

        monkeypatch.setattr(agent_tools, "ensure_tools", lambda _: installation)
        original = definitions
        monkeypatch.setattr(agent_tools, "definitions", lambda: [])
        app = ResearchApp(tmp_path, config=config, runtime_factory=runtime)
        run_id = (await app.create({"simulated": False}))["run_id"]
        await app.call("chat", {"run_id": run_id, "text": "最初的需求", "message_id": "initial"})
        await settle(app, run_id, 1, transport)
        sessions = app.coordinator(run_id).store.paths.agent("interaction") / "sessions"
        session_file = next(sessions.glob("*/session.json"))
        before = json.loads(session_file.read_text())
        await app.close()
        monkeypatch.setattr(agent_tools, "definitions", original)
        upgraded = ResearchApp(tmp_path, config=config, runtime_factory=runtime)
        try:
            await upgraded.call(
                "chat",
                {
                    "run_id": run_id,
                    "text": "沿用刚才的要求，用新增工具检查",
                    "message_id": "next",
                },
            )
            await settle(upgraded, run_id, 3, transport)
            after = json.loads(session_file.read_text())
            assert after["session_id"] == before["session_id"]
            assert after["history"][: len(before["history"])] == before["history"]
            assert "bash" not in {t["name"] for t in transport.payloads[0]["tools"]}
            assert "bash" in {t["name"] for t in transport.payloads[1]["tools"]}
            assert "upgraded" in json.dumps(transport.payloads[2]["input"])
            await asyncio.sleep(0.1)
            assert len(transport.payloads) == 3  # Waiting spends no further model calls.
            detail = await upgraded.detail(run_id)
            assert [m["text"] for m in detail["messages"]] == [
                "最初的需求",
                "原有回复",
                "沿用刚才的要求，用新增工具检查",
                "已检查",
            ]
        finally:
            await upgraded.close()

    asyncio.run(scenario())
