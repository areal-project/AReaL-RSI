"""Make the evidence behind a targeted intervention explicit and followable."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from areal_rsi.api.research import CandidateSnapshot, VerifiedResult
from areal_rsi.base.storage import now
from areal_rsi.coordination.actions import STRING
from areal_rsi.coordination.continuous_guidance import ContinuousGuidanceActions
from areal_rsi.coordination.coordinator import effect_id
from areal_rsi.modules.agent_runtime import ToolResult
from areal_rsi.prompts import load_prompt

if TYPE_CHECKING:
    from areal_rsi.coordination.collaboration import CollaborationPlan


DECISION = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["local_fix", "hypothesis"]},
        "problem": {**STRING, "description": "接收者现在需要解决的具体问题。"},
        "evidence": {**STRING, "description": "哪些实际记录支持现在介入；哪些仍是推测。"},
        "question": {**STRING, "description": "下一次尝试要验证或区分什么。"},
        "followup": {
            **STRING,
            "description": "上次建议之后实际发生了什么；首次或尚不清楚时如实说明。",
        },
    },
    "required": ["kind", "problem", "evidence", "question", "followup"],
    "additionalProperties": False,
}


class InterventionReviewActions(ContinuousGuidanceActions):
    def definitions(self, agent_id: str, *, include_hidden: bool = False) -> list[dict[str, Any]]:
        tools = super().definitions(agent_id, include_hidden=include_hidden)
        return [
            {
                **tool,
                "parameters": {
                    **tool["parameters"],
                    "properties": {**tool["parameters"]["properties"], "decision": DECISION},
                    "required": [*tool["parameters"]["required"], "decision"],
                },
            }
            if tool["name"] == "advise_researcher"
            else tool
            for tool in tools
        ]

    def state(self, agent_id: str) -> dict[str, Any]:
        state = super().state(agent_id)
        if agent_id != "rsi":
            return state
        c = self.c
        contract = c.store.contract_digest()
        histories: dict[str, list[dict[str, Any]]] = {}
        for result in c.store.records("verifications", VerifiedResult):
            if result.phase != "search" or result.contract_digest != contract:
                continue
            owner = c.store.get(
                "candidates", result.candidate_id, CandidateSnapshot
            ).source_agent_id
            histories.setdefault(owner, []).append(
                {
                    "created_at": result.created_at,
                    "result_id": result.result_id,
                    "candidate_id": result.candidate_id,
                    "passed": result.passed,
                    "status": result.status,
                    "score": result.score,
                    "withdrawn": c.store.is_withdrawn(result.candidate_id),
                }
            )
        for researcher in state["researchers"]:
            identifier = researcher["agent_id"]
            history = sorted(histories.get(identifier, []), key=lambda result: result["created_at"])
            researcher["recent_verified_results"] = history[-6:]
            researcher["previous_decision"] = c.state.get("guidance_decisions", {}).get(identifier)
        return state

    async def _apply(self, agent_id: str, key: str, name: str, args: dict[str, Any]) -> ToolResult:
        if name != "advise_researcher":
            return await super()._apply(agent_id, key, name, args)
        decision = args["decision"]
        if decision["kind"] not in {"local_fix", "hypothesis"} or any(
            not decision[field].strip() for field in ("problem", "evidence", "question", "followup")
        ):
            raise ValueError("请简要写清介入依据、待验证的问题和上次建议的结果")
        result = await super()._apply(
            agent_id,
            key,
            name,
            {field: value for field, value in args.items() if field != "decision"},
        )
        value = {
            "kind": "guidance_decision",
            "created_at": now(),
            "agent_id": "rsi",
            "recipient": args["agent_id"],
            "decision": decision,
            "message_id": result.output["message_id"],
            "references": args["references"],
        }
        ref = self.c.store.put("materials", effect_id("decision", key), value)
        self.c.state.setdefault("guidance_decisions", {})[args["agent_id"]] = {
            "uri": ref.uri,
            **value,
        }
        self.c.save()
        return result


def apply(plan: CollaborationPlan) -> CollaborationPlan:
    actions = type("ReviewedGuidanceActions", (InterventionReviewActions, plan.actions), {})
    return replace(
        plan,
        actions=actions,
        engine=type("ReviewedGuidanceEngine", (plan.engine,), {"state_actions": actions}),
        rsi_prompt=plan.rsi_prompt.replace(
            "{{tool_instructions}}",
            load_prompt("agents/rsi/intervention_review.md") + "\n\n{{tool_instructions}}",
        ),
    )
