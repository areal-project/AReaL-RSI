"""Active supervision with run-local progress, on top of independent attempts."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from areal_rsi.api.base import AgentState
from areal_rsi.base.storage import now
from areal_rsi.coordination.coordinator import effect_id
from areal_rsi.coordination.leader_guidance import GuidedPassKActions
from areal_rsi.coordination.passk import PassKEngine
from areal_rsi.prompts import load_prompt

if TYPE_CHECKING:
    from areal_rsi.coordination.collaboration import CollaborationPlan


class ContinuousGuidanceActions(GuidedPassKActions):
    def definitions(self, agent_id: str, *, include_hidden: bool = False) -> list[dict[str, Any]]:
        tools = super().definitions(agent_id, include_hidden=include_hidden)
        return [tool for tool in tools if tool["name"] != "wait"] if agent_id == "rsi" else tools

    def state(self, agent_id: str) -> dict[str, Any]:
        state = super().state(agent_id)
        if agent_id != "rsi":
            return state
        c = self.c
        path = c.store.root / "coordination/jobs.json"
        jobs = json.loads(path.read_text()) if path.exists() else {}
        for researcher in state["researchers"]:
            identifier = researcher["agent_id"]
            researcher["recent_progress"] = c.state.get("research_progress", {}).get(identifier, [])
            researcher["activity"] = c.state.get("agent_activity", {}).get(identifier)
            researcher["recent_jobs"] = [
                {
                    "job_id": job_id,
                    "created_at": info["created_at"],
                    "parameters": info.get("parameters"),
                    "result": info.get("result"),
                    "candidate_id": info.get("published_candidate_id") or info.get("candidate_id"),
                }
                for job_id, info in list(jobs.items())[::-1]
                if info["agent_id"] == identifier
            ][:3]
            if advice := researcher["latest_advice"]:
                researcher["latest_advice"] = {
                    **advice,
                    "acknowledged": advice["message_id"] in c.state["delivered"],
                }
        return state


class ContinuousGuidanceEngine(PassKEngine):
    state_actions = ContinuousGuidanceActions

    async def _event(self, agent_id: str, session: Path, event: dict[str, Any]) -> None:
        if (
            event["type"] == "model_output"
            and agent_id not in {"rsi", "interaction"}
            and (text := event.get("text", "").strip())
        ):
            c = self.coordinator
            value = {
                "agent_id": agent_id,
                "created_at": now(),
                "kind": "researcher_progress",
                "text": text,
            }
            # These are the researcher's own words, not verified conclusions.
            # Keep a short view plus immutable originals, without creating reports.
            ref = c.store.put("materials", effect_id("progress", json.dumps(value)), value)
            recent = c.state.setdefault("research_progress", {}).setdefault(agent_id, [])
            recent.append({"uri": ref.uri, **value, "text": text[:1600]})
            del recent[:-3]
            c.save()
        await super()._event(agent_id, session, event)

    async def tick(self) -> int:
        c = self.coordinator
        self._acquire()
        if (
            c.state["status"] == "running"
            and c.state.get("passk_ready")
            and c.actor("rsi").state == AgentState.WAITING
            and "rsi" not in c.state["active"]
            and not c.state["inboxes"].get("rsi")
        ):
            # A native final response is a turn boundary, not permission to go idle.
            sequence = c.state.get("guidance_turn", 0) + 1
            c.state["guidance_turn"] = sequence
            c.enqueue(
                "system",
                "rsi",
                load_prompt("agents/rsi/leader_guidance_v0.2.4_continue.md"),
                key=f"guidance-observe:{sequence}",
            )
        return await super().tick()

    def _recovery_state(self, agent_id: str) -> dict[str, Any]:
        if agent_id != "rsi":
            return super()._recovery_state(agent_id)
        state = self.state_actions(self.coordinator).state(agent_id)
        state.pop("brief", None)
        return state


def apply(plan: CollaborationPlan) -> CollaborationPlan:
    return replace(
        plan,
        actions=ContinuousGuidanceActions,
        engine=ContinuousGuidanceEngine,
        research_prompt=load_prompt("agents/research/leader_guidance_v0.2.4.md"),
        rsi_prompt=load_prompt("agents/rsi/leader_guidance_v0.2.4.md"),
    )
