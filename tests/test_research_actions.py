from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from areal_rsi.api.base import AgentState
from areal_rsi.api.research import ResearchLimits, TaskBrief
from areal_rsi.coordination.actions import ResearchActions
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.modules.responses import ToolCall


def test_role_permissions_and_report_stop(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Coordinator.create(tmp_path, TaskBrief("Study", "No baseline", ResearchLimits()))
        c.start()
        actions = ResearchActions(c)
        assignment = await actions.handler("rsi")(
            ToolCall(
                "spawn_researcher",
                {
                    "direction": "Environment",
                    "instruction": "Inspect the configured environment",
                    "completion_criteria": "Report exact supported interface",
                    "references": [],
                },
                "spawn-1",
            )
        )
        researcher = assignment.output["agent_id"]
        denied = await actions.handler(researcher)(ToolCall("spawn_researcher", {}, "forbidden"))
        assert "error" in denied.output
        report: dict[str, Any] = {
            "task_status": "Completed",
            "work": "Compared configured command and docs",
            "problems": "GPU unavailable",
            "results": "Command accepts the expected arguments",
            "interpretation": "Interface matches; execution is untested",
            "next_steps": "Provision GPU and execute one smoke job",
            "shared_findings": [],
            "references": [],
        }
        result = await actions.handler(researcher)(ToolCall("submit_report", report, "report-1"))
        assert result.stop and result.reason == "rsi_reply"
        assert c.actor(researcher).state == AgentState.WAITING
        repeated = await actions.handler(researcher)(ToolCall("submit_report", report, "report-1"))
        assert repeated == result
        assert c.actor(researcher).iterations[-1].report_ref is not None

    asyncio.run(scenario())


def test_workspace_escape_and_scope_are_enforced(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Coordinator.create(
            tmp_path, TaskBrief("Study", "Only skills", ResearchLimits(allowed_paths=("skills/*",)))
        )
        c.start()
        assignment = c.spawn(
            key="a",
            direction="A",
            instruction="Write a skill",
            completion_criteria="Save the candidate",
        )
        agent_id = assignment["agent_id"]
        actions = ResearchActions(c)
        handler = actions.handler(agent_id)
        outside = tmp_path / "private.txt"
        outside.write_text("private")
        (actions.workspace(agent_id) / "link").symlink_to(outside)
        denied = await handler(ToolCall("read_file", {"path": "link"}, "escape"))
        assert "error" in denied.output
        (actions.workspace(agent_id) / "link").unlink()
        await handler(ToolCall("write_file", {"path": "prompt.md", "content": "Changed"}, "write"))
        result = await handler(ToolCall("save_candidate", {"description": "Illegal scope"}, "save"))
        assert "outside" in result.output["error"]
        assert not (c.store.root / "candidates").exists()

    asyncio.run(scenario())


def test_interaction_keeps_natural_brief_before_start(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Coordinator.create(tmp_path, TaskBrief("Unclear goal", "Initial words"))
        c.enqueue("user", "interaction", "Please clarify my goal", key="user-1")
        assert c.pending() == ["interaction"]
        actions = ResearchActions(c)
        updated = await actions.handler("interaction")(
            ToolCall(
                "update_brief",
                {
                    "brief_json": '{"objective":"Optimize a reusable kernel skill",'
                    '"instructions":"Prefer simple approaches; fusion is only a suggestion",'
                    '"limits":{"allowed_paths":["skills/*"]}}',
                },
                "brief-1",
            )
        )
        assert "error" not in updated.output
        assert c.store.brief().instructions.endswith("only a suggestion")
        assert c.store.brief().limits.allowed_paths == ("skills/*",)
        c.start()
        refused = await actions.handler("interaction")(
            ToolCall(
                "update_brief",
                {
                    "brief_json": '{"objective":"Different goal","instructions":"Change limits"}',
                },
                "brief-2",
            )
        )
        assert "frozen" in refused.output["error"]

    asyncio.run(scenario())
