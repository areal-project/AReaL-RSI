"""Limit leader-mediated spread of a recorded experience to one new researcher."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from areal_rsi.api.base import RecordRef
from areal_rsi.base.storage import now
from areal_rsi.coordination.actions import STRING
from areal_rsi.coordination.continuous_guidance import ContinuousGuidanceActions
from areal_rsi.coordination.coordinator import effect_id
from areal_rsi.modules.agent_runtime import ToolResult
from areal_rsi.prompts import load_prompt

if TYPE_CHECKING:
    from areal_rsi.coordination.collaboration import CollaborationPlan


EXPERIENCE = {
    "anyOf": [
        {"type": "null"},
        {
            "type": "object",
            "properties": {
                "id": {
                    **STRING,
                    "description": "本 run 内同一结论沿用同一简短名称，补充和纠正也不另起名称。",
                },
                "source_agent_id": STRING,
                "conclusion": STRING,
                "conditions": STRING,
                "parents": {
                    "type": "array",
                    "items": STRING,
                    "description": "若来自已分享经验，填写原经验 id；否则 []。",
                },
            },
            "required": ["id", "source_agent_id", "conclusion", "conditions", "parents"],
            "additionalProperties": False,
        },
    ],
    "description": "传递同伴经验时登记来源和结论；"
    "仅依据接收者自己的记录或独立调研给建议时为 null。",
}


class SharingQuotaActions(ContinuousGuidanceActions):
    def definitions(self, agent_id: str, *, include_hidden: bool = False) -> list[dict[str, Any]]:
        tools = super().definitions(agent_id, include_hidden=include_hidden)
        return [
            {
                **tool,
                "parameters": {
                    **tool["parameters"],
                    "properties": {**tool["parameters"]["properties"], "experience": EXPERIENCE},
                    "required": [*tool["parameters"]["required"], "experience"],
                },
            }
            if tool["name"] == "advise_researcher"
            else tool
            for tool in tools
        ]

    def state(self, agent_id: str) -> dict[str, Any]:
        state = super().state(agent_id)
        if agent_id == "rsi":
            state["shared_experiences"] = list(self.c.state.get("shared_experiences", {}).values())
        elif agent_id != "interaction":
            # Budget/status queries should not broadcast everyone else's reports.
            # Explicit current-run trace and record reads remain available.
            for researcher in state.get("researchers", []):
                if researcher["agent_id"] != agent_id:
                    researcher.pop("latest_report", None)
        return state

    def _owner(self, ref: RecordRef) -> str | None:
        value = self.c.store.public_record(ref)
        if candidate := value.get("candidate_id"):
            value = self.c.store.public_record(
                self.c.store.record_reference(RecordRef("candidates/" + candidate))
            )
        elif job_id := value.get("job_id"):
            value = self.c.store.public_record(
                self.c.store.record_reference(RecordRef("executions/" + job_id))
            )
        owner: str | None = value.get("source_agent_id") or value.get("agent_id")
        return owner

    async def _apply(self, agent_id: str, key: str, name: str, args: dict[str, Any]) -> ToolResult:
        if name != "advise_researcher":
            return await super()._apply(agent_id, key, name, args)
        target = args["agent_id"]
        researchers = {actor.agent_id for actor in self.c.store.run().research_agents}
        if target not in researchers:
            raise ValueError("Unknown researcher")
        refs = self.handoff_refs(args["message"], args["references"])
        owners = {self._owner(ref) for ref in refs} & researchers
        experience = args["experience"]
        ledger = self.c.state.get("shared_experiences", {})
        touched: dict[str, dict[str, Any]] = {}
        if experience is None:
            if owners - {target}:
                raise ValueError("引用同伴记录的建议需要填写 experience，不能按独立建议发送")
        else:
            identifier, source = experience["id"].strip(), experience["source_agent_id"]
            if not identifier or source not in researchers:
                raise ValueError("经验需要非空 id 和当前 run 的来源研究员")
            parents = list(dict.fromkeys(experience["parents"]))
            entry = ledger.get(identifier)
            if entry:
                if source != entry["source_agent_id"]:
                    raise ValueError("同一经验不能更换来源")
                parents = list(dict.fromkeys([*entry["parents"], *parents]))
            elif source not in owners:
                raise ValueError("新经验需要引用来源研究员的原始记录")
            if identifier in parents:
                raise ValueError("经验不能引用自身为来源")
            pending = list(parents)
            while pending:
                parent = pending.pop()
                if parent in touched:
                    continue
                if parent not in ledger:
                    raise ValueError("未知原经验：" + parent)
                if parent == identifier:
                    raise ValueError("经验来源不能形成循环")
                touched[parent] = ledger[parent]
                pending.extend(ledger[parent]["parents"])
            covered = {source, target, *(item["source_agent_id"] for item in touched.values())}
            if owners - covered:
                raise ValueError("一次只分享一条经验；其他来源的经验请分别登记或引用 parents")
            touched[identifier] = {
                **(entry or {}),
                **experience,
                "id": identifier,
                "parents": parents,
                "references": [ref.uri for ref in refs],
                "recipient": entry["recipient"] if entry else None,
            }
            for item in touched.values():
                if target != item["source_agent_id"] and item["recipient"] not in {None, target}:
                    raise ValueError(
                        f"经验 {item['id']} 已分享给 {item['recipient']}；不能增加接收者。"
                        "可向原接收者补充或纠正，也可继续观察其他研究员。"
                    )
        result = await super()._apply(
            agent_id,
            key,
            name,
            {name: value for name, value in args.items() if name != "experience"},
        )
        if touched:
            ledger = self.c.state.setdefault("shared_experiences", {})
            for identifier, item in touched.items():
                ledger[identifier] = {
                    **item,
                    "recipient": target if target != item["source_agent_id"] else item["recipient"],
                }
            self.c.store.put(
                "materials",
                effect_id("experience", key),
                {
                    "kind": "shared_experience",
                    "created_at": now(),
                    **touched[experience["id"].strip()],
                    "recipient": target,
                    "message_id": result.output["message_id"],
                },
            )
            self.c.save()
        return result


def apply(plan: CollaborationPlan) -> CollaborationPlan:
    actions = type("LimitedSharingActions", (SharingQuotaActions, plan.actions), {})
    return replace(
        plan,
        actions=actions,
        engine=type("LimitedSharingEngine", (plan.engine,), {"state_actions": actions}),
        rsi_prompt=plan.rsi_prompt.replace(
            "{{tool_instructions}}",
            load_prompt("agents/rsi/sharing_quota.md") + "\n\n{{tool_instructions}}",
        ),
        research_prompt=plan.research_prompt.replace(
            "{{tool_instructions}}",
            load_prompt("agents/research/sharing_quota.md") + "\n\n{{tool_instructions}}",
        ),
    )
