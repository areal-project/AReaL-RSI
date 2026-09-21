"""Persona-aware, factual sharing on top of the existing report/message path."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from areal_rsi.coordination.problem_sharing import ProblemSharingActions
from areal_rsi.modules.agent_runtime import ToolResult
from areal_rsi.prompts import load_prompt

if TYPE_CHECKING:
    from areal_rsi.coordination.collaboration import CollaborationPlan


TOOL_PROMPTS = {
    "advise_researcher": "agents/rsi/advise_researcher_v0.2.1.3.md",
    "submit_report": "agents/research/submit_report_v0.2.1.3.md",
}


class ObjectiveSharingActions(ProblemSharingActions):
    def definitions(self, agent_id: str, *, include_hidden: bool = False) -> list[dict[str, Any]]:
        return [
            {**tool, "description": load_prompt(TOOL_PROMPTS[tool["name"]]).strip()}
            if tool["name"] in TOOL_PROMPTS
            else tool
            for tool in super().definitions(agent_id, include_hidden=include_hidden)
        ]

    def _identity(self, agent_id: str) -> str:
        if agent_id == "rsi":
            return "RSI Agent"
        profile = self.c.state.get("researcher_profiles", {}).get(agent_id, {})
        return f"{agent_id}（{profile['name']}）" if profile else agent_id

    async def _apply(self, agent_id: str, key: str, name: str, args: dict[str, Any]) -> ToolResult:
        if name == "advise_researcher":
            experience = args["experience"]
            source = experience["source_agent_id"] if experience else "rsi"
            target = args["agent_id"]
            profile = self.c.state.get("researcher_profiles", {}).get(target, {})
            args = {
                **args,
                "message": load_prompt(
                    "agents/rsi/shared_experience_v0.2.1.3.md",
                    source=self._identity(source),
                    recipient=self._identity(target),
                    preference=profile.get("reminder", ""),
                    message=args["message"],
                ).strip(),
            }
        # The parent still checks evidence ownership and the problem-sharing quota,
        # then persists the enriched message through the normal nonblocking inbox.
        return await super()._apply(agent_id, key, name, args)


def apply(plan: CollaborationPlan) -> CollaborationPlan:
    return replace(
        plan,
        actions=ObjectiveSharingActions,
        rsi_prompt=load_prompt("agents/rsi/leader_guidance_v0.2.1.3.md"),
        research_prompt=load_prompt("agents/research/leader_guidance_v0.2.1.3.md"),
    )
