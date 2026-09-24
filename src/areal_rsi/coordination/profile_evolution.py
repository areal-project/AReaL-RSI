"""v0.3: leader-selected persona bundles and a bounded, durable post-task review."""

from __future__ import annotations

import asyncio
import fcntl
import json
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from areal_rsi.api.base import RecordRef
from areal_rsi.base.storage import encode, now, write_json
from areal_rsi.coordination.actions import STRING, ResearchActions, definition
from areal_rsi.coordination.coordinator import effect_id
from areal_rsi.coordination.leader_guidance import TRACE, GuidedPassKActions
from areal_rsi.coordination.profile_resources import prepare_profiles
from areal_rsi.modules.agent_runtime import AgentRuntime, ToolCall, ToolResult
from areal_rsi.profile_library import digest, load_library, publish, revise
from areal_rsi.prompts import load_prompt

if TYPE_CHECKING:
    from areal_rsi.coordination.collaboration import CollaborationPlan
    from areal_rsi.coordination.config import ResearchConfig
    from areal_rsi.coordination.coordinator import Coordinator

READ_PROFILE = definition(
    "read_profile", "读取本次冻结的人设 prompt、知识库和技能全文。", profile_id=STRING
)
START = definition(
    "start_researchers",
    "根据任务选择人设并启动研究员；profile_ids 按启动顺序填写，允许重复，数量须等于 max_agents。",
    profile_ids={"type": "array", "items": STRING},
    reason=STRING,
)
FINISH = definition(
    "finish_profile_review",
    "提交人设复盘并更新共享人设库。无可靠改进时 updates=[]，仍需说明复盘结论。",
    summary=STRING,
    updates={
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "profile_id": STRING,
                "reason": STRING,
                "reminder": STRING,
                "evidence": {"type": "array", "items": STRING},
                "files": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"path": STRING, "content": STRING},
                        "required": ["path", "content"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["profile_id", "reason", "reminder", "evidence", "files"],
            "additionalProperties": False,
        },
    },
)
TERMINAL = {"completed", "conflict", "failed", "skipped"}


def freeze_library(config: ResearchConfig, root: Path) -> None:
    if config.researcher_profile_ids or config.metadata.get("researcher_profiles"):
        raise ValueError(
            "v0.3 由 leader 选择人设，请移除固定 researcher_profile_ids/metadata 人设列表"
        )
    directory = Path(config.profile_library_dir or root / "profile-library").expanduser().resolve()
    config.profile_library_dir = str(directory)
    config.metadata["profile_library"] = load_library(directory)


def initialize(actions: ResearchActions, config: ResearchConfig) -> None:
    c = actions.c
    library = config.metadata["profile_library"]
    selection = c.state.get("profile_selection")
    if selection:
        actions.researcher_profiles = tuple(
            prepare_profiles([library[p] for p in selection["profile_ids"]])
        )
    else:
        c.state.setdefault("profile_evolution", {"status": "pending"})
        c.save()
    # Only this run's frozen assets are visible to the research leader.
    write_json(c.store.root / "coordination/profile-library.json", library, immutable=True)


class ProfileEvolutionActions(GuidedPassKActions):
    def library(self) -> dict[str, dict[str, Any]]:
        return dict(
            json.loads((self.c.store.root / "coordination/profile-library.json").read_text())
        )

    def definitions(self, agent_id: str, *, include_hidden: bool = False) -> list[dict[str, Any]]:
        tools = super().definitions(agent_id, include_hidden=include_hidden)
        if agent_id != "rsi":
            return tools
        return [START if t["name"] == "start_researchers" else t for t in tools] + [READ_PROFILE]

    def state(self, agent_id: str) -> dict[str, Any]:
        state = super().state(agent_id)
        if agent_id == "rsi":
            state["available_profiles"] = [
                {
                    "id": p["id"],
                    "name": p["name"],
                    "reminder": p["reminder"],
                    "files": sorted(p["resources"]),
                }
                for p in self.library().values()
            ]
            state["profile_selection"] = self.c.state.get("profile_selection")
        return state

    async def _apply(self, agent_id: str, key: str, name: str, args: dict[str, Any]) -> ToolResult:
        if name == "read_profile":
            return ToolResult(self.library()[args["profile_id"]])
        if name == "start_researchers":
            identifiers, reason = args["profile_ids"], args["reason"]
            library = self.library()
            if (
                len(identifiers) != self.c.store.brief().limits.max_agents
                or any(p not in library for p in identifiers)
                or not reason.strip()
            ):
                raise ValueError("请选择 max_agents 个人设 ID，并说明与任务相关的选择理由")
            selection = self.c.state.get("profile_selection")
            if selection and identifiers != selection["profile_ids"]:
                raise ValueError("本次人设选择已冻结，不能改变已启动研究员的身份")
            profiles = prepare_profiles([library[p] for p in identifiers])
            self.researcher_profiles = tuple(profiles)
            if not selection:
                self.c.state["profile_selection"] = {
                    "profile_ids": identifiers,
                    "reason": reason,
                    "selected_at": now(),
                    "revisions": {p: digest(library[p]) for p in set(identifiers)},
                }
                self.c.save()
                self.c.store.event("profiles_selected", profile_ids=identifiers, reason=reason)
            write_json(
                self.c.store.root / "coordination/selected-profiles.json",
                profiles,
                immutable=True,
            )
        return await super()._apply(agent_id, key, name, args)


def apply(plan: CollaborationPlan) -> CollaborationPlan:
    return replace(
        plan,
        actions=type("EvolvingProfileActions", (ProfileEvolutionActions, plan.actions), {}),
        rsi_prompt=plan.rsi_prompt + load_prompt("agents/rsi/profile_selection_v0.3.md"),
    )


async def review_profiles(
    c: Coordinator, config: ResearchConfig, runtime: AgentRuntime, actions: ResearchActions
) -> None:
    if c.state.get("profile_evolution", {}).get("status") in TERMINAL:
        return
    if c.state.get("user_stopped") or not c.state.get("passk_ready"):
        c.state["profile_evolution"] = {"status": "skipped", "reason": "用户停止或研究员尚未启动"}
        c.save()
        return
    folder = c.store.root / "profile-evolution"
    folder.mkdir(exist_ok=True)
    with (folder / ".lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        await _review(c, config, runtime, actions, folder)


async def _review(
    c: Coordinator,
    config: ResearchConfig,
    runtime: AgentRuntime,
    actions: ResearchActions,
    folder: Path,
) -> None:
    assert isinstance(actions, ProfileEvolutionActions)
    selected = c.state["profile_selection"]["profile_ids"]
    library = {p: config.metadata["profile_library"][p] for p in set(selected)}
    # The same leader/model receives a fresh review session, after exploration and verification.
    context = {
        "run_id": c.store.run_id,
        "brief": encode(c.store.brief()),
        "selection": c.state["profile_selection"],
        "researchers": actions.state("rsi")["researchers"],
        "profiles": library,
        "finalization": c.state.get("finalization"),
        "sessions": {
            a.agent_id: [
                p.name
                for p in (c.store.paths.agent(a.agent_id) / "sessions").glob("*")
                if p.is_dir()
            ]
            for a in c.store.run().research_agents
        },
    }
    write_json(folder / "input.json", context)
    c.state["profile_evolution"] = {"status": "running", "started_at": now()}
    c.save()
    c.store.event("profile_review_started")
    read_record = definition(
        "read_record", "读取本 run 的原始报告、验证记录或轨迹片段。", uri=STRING
    )
    trace = definition(
        "read_researcher_trace",
        TRACE["description"],
        agent_id=STRING,
        offset={"type": "integer", "minimum": -1},
        session_id=STRING,
    )

    def commit(summary: str, updates: list[dict[str, Any]]) -> ToolResult:
        if not summary.strip():
            raise ValueError("请总结身份 prompt、知识库和 skills 三方面的复盘结论")
        for update in updates:
            for uri in update["evidence"]:
                ref = c.store.record_reference(RecordRef(uri))
                c.store.public_record(ref)  # Reject missing or cross-run evidence.
        revised = revise(library, updates)
        proposal = {"summary": summary, "updates": updates}
        write_json(folder / "proposal.json", proposal, immutable=True)
        assert config.profile_library_dir
        result = publish(
            Path(config.profile_library_dir), c.store.run_id, library, revised, proposal
        )
        write_json(folder / "review.json", result, immutable=True)
        c.state["profile_evolution"] = result
        c.save()
        c.store.event(
            "profile_review_finished", status=result["status"], published=result["published"]
        )
        return ToolResult(result, stop=True, reason="profile_review_completed")

    async def handle(call: ToolCall) -> ToolResult:
        try:
            args = call.arguments
            if call.name == "read_profile":
                return ToolResult(library[args["profile_id"]])
            if call.name == "read_record":
                ref = c.store.record_reference(RecordRef(args["uri"]))
                return ToolResult(c.store.public_record(ref))
            if call.name == "read_researcher_trace":
                agent = args["agent_id"]
                session = args["session_id"]
                if agent not in context["sessions"] or session not in context["sessions"][agent]:
                    raise ValueError("只能读取本次任务列出的研究员会话")
                return GuidedPassKActions(c).trace(
                    agent,
                    effect_id("profile_trace", call.call_id),
                    args["offset"],
                    session_id=session,
                )
            if call.name == "finish_profile_review":
                return commit(args["summary"], args["updates"])
            raise ValueError("复盘只支持读取本 run 证据和提交人设更新")
        except (ValueError, KeyError, FileNotFoundError) as exc:
            return ToolResult({"error": str(exc)})

    try:
        # Replay a prepared commit without asking the model to regenerate it after a crash.
        if (folder / "proposal.json").exists():
            proposal = json.loads((folder / "proposal.json").read_text())
            commit(proposal["summary"], proposal["updates"])
            return
        prompt = load_prompt("agents/rsi/profile_review_v0.3.md")
        write_json(folder / "prompt.json", {"prompt": prompt}, immutable=True)
        async with asyncio.timeout(300):
            result = await runtime.turn(
                c.store.paths.agent("rsi") / "sessions" / "profile-review-v03",
                system_prompt=prompt,
                user_message=json.dumps(context, ensure_ascii=False),
                message_id="profile-review-v03",
                tools=[READ_PROFILE, read_record, trace, FINISH],
                handler=handle,
                workspace=folder,
                recovery_state={"agent_id": "rsi", "phase": "profile_review"},
            )
        write_json(folder / "response.json", encode(result))
        if c.state["profile_evolution"]["status"] == "running":
            raise ValueError("leader 未调用 finish_profile_review，未发布任何人设更新")
    except asyncio.CancelledError:
        if c.state["profile_evolution"]["status"] == "running":
            c.state["profile_evolution"]["status"] = "interrupted"
            c.save()
        raise
    except Exception as exc:
        if c.state["profile_evolution"]["status"] == "running":
            c.state["profile_evolution"].update(
                status="failed", error=str(exc) or type(exc).__name__
            )
            c.save()
            c.store.event("profile_review_failed", error=str(exc) or type(exc).__name__)
