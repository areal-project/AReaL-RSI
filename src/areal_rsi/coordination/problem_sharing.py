"""Keep unresolved problems local; allow verified solutions to travel further."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from areal_rsi.api.base import RecordRef
from areal_rsi.coordination.actions import STRING
from areal_rsi.coordination.leader_guidance import GuidedPassKActions
from areal_rsi.coordination.passk import PassKEngine
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
                    "description": "同一经验始终沿用同一名称，补充、转述和解决后也不另起名称。",
                },
                "kind": {
                    "type": "string",
                    "enum": ["problem", "positive"],
                    "description": "problem：失败、风险或尚未解决的问题；positive："
                    "已验证的解决办法或其他可用成果。确认问题存在仍属于 problem。",
                },
                "source_agent_id": {
                    **STRING,
                    "description": "本次证据的来源研究员；leader 自己的发现填 rsi。",
                },
            },
            "required": ["id", "kind", "source_agent_id"],
            "additionalProperties": False,
        },
    ],
    "description": "分享经验时登记，并在 references 引用原始证据；"
    "仅围绕接收者自己的工作给普通建议时填 null。一次只分享一条经验。",
}


class ProblemSharingActions(GuidedPassKActions):
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
            state["shared_experiences"] = self.c.state.get("problem_sharing", {})
        else:
            # Status queries must not broadcast reports containing unresolved problems.
            # Explicit current-run record/trace reads remain available.
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
        elif job := value.get("job_id"):
            value = self.c.store.public_record(
                self.c.store.record_reference(RecordRef("executions/" + job))
            )
        owner: str | None = value.get("source_agent_id") or value.get("agent_id")
        return owner

    async def _apply(self, agent_id: str, key: str, name: str, args: dict[str, Any]) -> ToolResult:
        if name != "advise_researcher":
            return await super()._apply(agent_id, key, name, args)
        target = args["agent_id"]
        refs = self.handoff_refs(args["message"], args["references"])
        owners = {self._owner(ref) for ref in refs} - {None, "rsi"}
        experience = args["experience"]
        entry = None
        if experience is None:
            if owners - {target}:
                raise ValueError("分享同伴证据需要填写 experience")
        else:
            identifier = experience["id"].strip()
            source, kind = experience["source_agent_id"], experience["kind"]
            researchers = {actor.agent_id for actor in self.c.store.run().research_agents}
            if not identifier or source not in researchers | {"rsi"}:
                raise ValueError("经验需要非空 id 和本 run 的来源 agent")
            if kind not in {"problem", "positive"}:
                raise ValueError("经验类型必须为 problem 或 positive")
            if not refs or (source != "rsi" and source not in owners):
                raise ValueError("分享经验需要引用来源 agent 的原始记录")
            previous = self.c.state.get("problem_sharing", {}).get(identifier)
            entry = dict(
                previous
                or {
                    "id": identifier,
                    "origin_agent_id": source,
                    "problem_recipient": None,
                    "recipients": [],
                }
            )
            if kind == "problem" and target != entry["origin_agent_id"]:
                if entry["problem_recipient"] not in {None, target}:
                    raise ValueError(
                        f"问题经验 {identifier} 已分享给 {entry['problem_recipient']}；"
                        "解决前不能再增加接收者。可向原发现者和接收者补充，"
                        "有已验证的解决办法后沿用此 id，以 positive 分享并附新证据。"
                    )
                entry["problem_recipient"] = target
            uris = [ref.uri for ref in refs]
            if (
                previous
                and previous["kind"] == "problem"
                and kind == "positive"
                and not set(uris) - set(previous["references"])
            ):
                raise ValueError("问题转为正向经验需要新的解决或验证记录，不能只改分类")
            entry.update(kind=kind, source_agent_id=source, references=uris)
            entry["recipients"] = list(dict.fromkeys([*entry["recipients"], target]))
        result = await super()._apply(
            agent_id, key, name, {k: v for k, v in args.items() if k != "experience"}
        )
        if entry is not None:
            self.c.state.setdefault("problem_sharing", {})[entry["id"]] = entry
            self.c.save()
        return result


class ProblemSharingEngine(PassKEngine):
    def _recovery_state(self, agent_id: str) -> dict[str, Any]:
        state = super()._recovery_state(agent_id)
        if agent_id == "rsi":
            state["shared_experiences"] = self.coordinator.state.get("problem_sharing", {})
        return state


def apply(plan: CollaborationPlan) -> CollaborationPlan:
    return replace(
        plan,
        actions=ProblemSharingActions,
        engine=ProblemSharingEngine,
        rsi_prompt=load_prompt("agents/rsi/leader_guidance_v0.2.1.1.md"),
        research_prompt=load_prompt("agents/research/leader_guidance_v0.2.1.1.md"),
    )
