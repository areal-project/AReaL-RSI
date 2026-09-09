"""Role-scoped model tools; execution adapters register additional tools explicitly."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from areal_rsi.api.base import AgentState, RecordRef, RunState
from areal_rsi.api.research import CandidateSnapshot, EvidenceRecord, TaskBrief, VerifiedResult
from areal_rsi.base.storage import decode, encode, now, safe_relative, write_json
from areal_rsi.coordination.coordinator import Coordinator, effect_id
from areal_rsi.modules.responses import ToolCall, ToolHandler, ToolResult

STRING = {"type": "string"}
STRINGS = {"type": "array", "items": STRING}
REFERENCES = {
    "type": "array",
    "items": {
        "type": "string",
        "description": "工具返回的精确 run:// 记录 URI，不接受文件路径或网页地址。",
    },
    "description": "填写工具返回的 run:// 记录 URI。文件路径和网页地址写在正文中，"
    "或先用 fetch_url/save_evidence 归档后引用。没有记录引用时使用 []。",
}


def definition(name: str, tool_description: str, **properties: Any) -> dict[str, Any]:
    return {
        "type": "function",
        "name": name,
        "description": tool_description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
        "strict": True,
    }


COMMON = [
    definition(
        "get_state",
        "查看已保存的研究状态、预算与证据摘要；需要完整记录时，用返回的 URI 调用read_record。",
    ),
    definition("read_record", "用 run:// URI 读取你有权限查看的研究记录。", uri=STRING),
    definition(
        "read_candidate_file",
        "读取已保存候选中的文本文件，不切换工作区。提供 candidate_id 和相对文件路径。",
        candidate_id=STRING,
        path=STRING,
    ),
    definition("list_files", "列出当前工作区中的文件。"),
    definition("read_file", "读取工作区中的 UTF-8 文本文件。", path=STRING),
    definition(
        "write_file",
        "在工作区中新建或替换 UTF-8 文本文件。",
        path=STRING,
        content=STRING,
    ),
    definition(
        "save_evidence",
        "归档具体观察或发现，保留真实来源与局限。",
        title=STRING,
        summary=STRING,
        references=REFERENCES,
        limitations=STRING,
    ),
    definition(
        "save_candidate",
        "将当前工作区保存为不可变的候选版本，记录父版本。仅在需要保留产物时使用。",
        description=STRING,
    ),
    definition(
        "checkout_candidate",
        "从已保存的候选继续工作。若工作区还有未保存的修改，先保存快照；工具不会覆盖这些修改。",
        candidate_id=STRING,
    ),
]
RSI = [
    definition(
        "update_plan",
        "保存简明的当前研究计划，并说明调整原因。",
        plan=STRING,
        reason=STRING,
    ),
    definition(
        "spawn_researcher",
        "新建一个研究方向，向研究员下发第一项具体任务。",
        direction=STRING,
        instruction=STRING,
        completion_criteria=STRING,
        references=REFERENCES,
    ),
    definition(
        "assign",
        "针对研究员的最新报告作出回复，并下发下一项任务。",
        agent_id=STRING,
        instruction=STRING,
        completion_criteria=STRING,
        reply_to_report_id=STRING,
        references=REFERENCES,
    ),
    definition(
        "stop_researcher",
        "停止这个研究方向，系统会取消该研究员正在进行的执行。",
        agent_id=STRING,
        reason=STRING,
    ),
    definition(
        "stop_run",
        "根据现有证据结束研究，说明最佳结果、未达目标和停止原因；不能把停止记作成功。",
        reason=STRING,
    ),
    definition(
        "share_evidence",
        "将有用证据共享给本次研究中的其他方向，保留来源。",
        evidence_id=STRING,
        limitations=STRING,
    ),
    definition("wait", "结束本回合，等新消息到来后继续。说明需要等待的具体事件。", reason=STRING),
    definition(
        "ask_user",
        "直接向用户提问并结束本回合。说明问题、已做检查、影响和需要用户处理的事项；"
        "用户回答会直接回到你的同一会话。",
        question=STRING,
    ),
    definition(
        "finish",
        "核对交付物和所需最终验证后结束研究。candidate_id 为空时采用当前final/best 对应的候选。",
        summary=STRING,
        candidate_id=STRING,
    ),
]
RESEARCH = [
    definition(
        "submit_report",
        "提交当前任务报告并立即停止，等待 RSI 回复。",
        task_status=STRING,
        work=STRING,
        problems=STRING,
        results=STRING,
        interpretation=STRING,
        next_steps=STRING,
        shared_findings=STRINGS,
        references=REFERENCES,
    )
]
INTERACTION = [
    definition(
        "send_to_rsi",
        "用自然语言将用户指令、建议或回答及必要背景转交 RSI。",
        message=STRING,
    ),
    definition("ask_user", "向用户提出简短的问题，然后结束本回合。", question=STRING),
    definition(
        "update_brief",
        "启动前更新需求。先读取 get_state；brief_json 包含"
        "objective、instructions、limits 和可为空的"
        "verification，保留未修改字段。",
        brief_json=STRING,
    ),
]


class ResearchActions:
    def __init__(self, coordinator: Coordinator):
        self.c = coordinator
        self.extras: dict[str, tuple[dict[str, Any], Callable[[str], ToolHandler], set[str]]] = {}

    @staticmethod
    def role(agent_id: str) -> str:
        return agent_id if agent_id in {"rsi", "interaction"} else "research"

    def register(
        self,
        tool: dict[str, Any],
        factory: Callable[[str], ToolHandler],
        *,
        roles: set[str],
    ) -> None:
        self.extras[tool["name"]] = tool, factory, roles

    def definitions(self, agent_id: str) -> list[dict[str, Any]]:
        role = self.role(agent_id)
        common = COMMON if role != "interaction" else COMMON[:2]
        return [
            *common,
            *{"rsi": RSI, "interaction": INTERACTION, "research": RESEARCH}[role],
            *(tool for tool, _, roles in self.extras.values() if role in roles),
        ]

    def handler(self, agent_id: str) -> ToolHandler:
        async def handle(call: ToolCall) -> ToolResult:
            allowed = {tool["name"] for tool in self.definitions(agent_id)}
            if call.name not in allowed:
                return ToolResult({"error": "Tool is not available to this role"})
            actor = self.c.actor(agent_id)
            key = effect_id("action", agent_id + ":" + call.call_id)
            action_path = self.c.store.root / "actions" / f"{key}.json"
            stopping_replay = call.name == "stop_run" and action_path.exists()
            if (
                actor.state == AgentState.STOPPED
                and call.name != "submit_report"
                and not stopping_replay
            ):
                return ToolResult({"error": "Agent was stopped"}, stop=True, reason="stopped")
            if action_path.exists():
                previous = json.loads(action_path.read_text())
                if previous["name"] != call.name or previous["arguments"] != call.arguments:
                    raise ValueError("Tool call identity reused for different arguments")
                if previous["status"] == "completed":
                    return ToolResult(**previous["result"])
            else:
                self.c.store.put(
                    "actions",
                    key,
                    {
                        "name": call.name,
                        "arguments": call.arguments,
                        "status": "pending",
                        "agent_id": agent_id,
                        "created_at": now(),
                    },
                )
            try:
                if call.name in self.extras:
                    result = await self.extras[call.name][1](agent_id)(call)
                else:
                    result = await self._apply(agent_id, key, call.name, call.arguments)
            except (ValueError, KeyError, FileNotFoundError, FileExistsError) as exc:
                # Actionable tool errors let the agent correct its next call. Do
                # not catch cancellation or unknown failures after side effects.
                result = ToolResult({"error": str(exc)})
            self.c.store.put(
                "actions",
                key,
                {
                    "name": call.name,
                    "arguments": call.arguments,
                    "status": "completed",
                    "agent_id": agent_id,
                    "result": {
                        "output": result.output,
                        "stop": result.stop,
                        "reason": result.reason,
                    },
                },
                immutable=False,
            )
            return result

        return handle

    def workspace(self, agent_id: str) -> Path:
        root = self.c.store.paths.agent(agent_id) / "workspace"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def workspace_path(self, agent_id: str, name: str) -> Path:
        root = self.workspace(agent_id).resolve()
        path = root / safe_relative(name)
        if not path.resolve().is_relative_to(root):
            raise ValueError("Path escapes the agent workspace")
        return path

    def read_record(self, agent_id: str, uri: str) -> Any:
        value = self.c.store.public_record(RecordRef(uri))
        if self.role(agent_id) in {"rsi", "interaction"}:
            return value
        category = urlsplit(uri).path.split("/")[1]
        if category in {"briefs", "candidates", "verifications"}:
            return value
        if value.get("agent_id") == agent_id or value.get("shared"):
            return value
        actor = self.c.actor(agent_id)
        if any(node.report_ref and node.report_ref.uri == uri for node in actor.iterations):
            return value
        if any(uri == ref.uri for node in actor.iterations for ref in node.references):
            return value
        if any(
            uri == ref.uri for item in self.c.store.evidence(agent_id) for ref in item.references
        ):
            return value
        raise ValueError("Evidence is not shared with this research direction")

    def refs(self, agent_id: str, values: list[str]) -> tuple[RecordRef, ...]:
        for uri in values:
            self.read_record(agent_id, uri)
        return tuple(RecordRef(uri) for uri in values)

    async def _apply(
        self,
        agent_id: str,
        key: str,
        name: str,
        args: dict[str, Any],
    ) -> ToolResult:
        c = self.c
        if name == "get_state":
            summary = c.summary()
            if agent_id == "interaction":
                ids = c.current_message_ids(agent_id)
                summary["current_message_id"] = ids[-1] if ids else None
                summary["current_message_ids"] = ids
            if self.role(agent_id) == "research":
                summary["agents"] = [encode(c.actor(agent_id))]
                summary.pop("waiting_for_user", None)
            summary["evidence"] = [
                self.evidence_public(item, preview=True) for item in c.store.evidence(agent_id)
            ]
            summary["seed_candidate_id"] = c.state["seed_candidate_id"]
            summary["workspace_candidate_id"] = self.workspace_candidate(agent_id)
            summary["require_final_confirmation"] = self.require_final_confirmation()
            seed_id = c.state["seed_candidate_id"]
            if seed_id is not None:
                seed = c.store.get("candidates", seed_id, CandidateSnapshot)
                summary["seed_candidate"] = {
                    **encode(seed),
                    "uri": c.store.paths.reference(
                        c.store.root / "candidates" / f"{seed_id}.json"
                    ).uri,
                }
            return ToolResult(summary)
        if name == "read_record":
            return ToolResult(self.read_record(agent_id, args["uri"]))
        if name == "read_candidate_file":
            candidate = c.store.get("candidates", args["candidate_id"], CandidateSnapshot)
            manifest = await c.store.content.read_json(candidate.content)
            assert isinstance(manifest, dict) and isinstance(manifest["files"], dict)
            relative_name = safe_relative(args["path"]).as_posix()
            from areal_rsi.api.base import ContentRef

            ref = decode(ContentRef, manifest["files"][relative_name])
            text = await c.store.content.read_text(ref)
            if len(text) > 256000:
                raise ValueError("Candidate file is too large for the model context")
            return ToolResult({"path": relative_name, "content": text})
        if name == "list_files":
            root = self.workspace(agent_id)
            return ToolResult(
                {
                    "files": [
                        p.relative_to(root).as_posix()
                        for p in sorted(root.rglob("*"))
                        if p.is_file()
                    ][:1000]
                }
            )
        if name == "read_file":
            path = self.workspace_path(agent_id, args["path"])
            if path.stat().st_size > 256_000:
                raise ValueError("File is too large for context; use a job to summarize it")
            return ToolResult({"path": args["path"], "content": path.read_text()})
        if name == "write_file":
            path = self.workspace_path(agent_id, args["path"])
            if len(args["content"].encode()) > 1_000_000:
                raise ValueError("Text file exceeds tool limit")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(args["content"])
            return ToolResult({"path": args["path"], "bytes": path.stat().st_size})
        if name == "update_plan":
            c.state["plan"] = args["plan"]
            c.save()
            c.store.event(
                "plan_updated", agent_id=agent_id, plan=args["plan"], reason=args["reason"]
            )
            return ToolResult({"saved": True})
        if name in {"spawn_researcher", "assign"}:
            values = {**args, "references": self.refs(agent_id, args["references"]), "key": key}
            result = c.spawn(**values) if name == "spawn_researcher" else c.assign(**values)
            await self.initialize_workspace(result["agent_id"])
            return ToolResult(result)
        if name == "submit_report":
            values = {
                **args,
                "references": self.refs(agent_id, args["references"]),
                "shared_findings": tuple(args["shared_findings"]),
            }
            result = c.report(agent_id, key=key, fields=values)
            return ToolResult(result, stop=True, reason="rsi_reply")
        if name == "stop_researcher":
            c.stop_agent(args["agent_id"], args["reason"])
            return ToolResult({"stopped": args["agent_id"]})
        if name == "stop_run":
            reason = args["reason"].strip()
            if not reason:
                raise ValueError(
                    "Stopping requires an evidence-based reason and remaining limitation"
                )
            if c.state["status"] not in {"planned", "running", "paused", "stopped"}:
                raise ValueError("This run has already concluded")
            c.stop(reason)
            message_id = c.enqueue("rsi", "user", reason, key=key)
            return ToolResult(
                {"status": "stopped", "reason": reason, "message_id": message_id},
                stop=True,
                reason="stopped",
            )
        if name == "wait":
            c.wait(agent_id, args["reason"])
            return ToolResult({"waiting_for": args["reason"]}, stop=True, reason="message")
        if name == "ask_user":
            message_id = c.ask_user(agent_id, args["question"], key=key)
            return ToolResult(
                {"question_sent": True, "message_id": message_id}, stop=True, reason="user"
            )
        if name == "send_to_rsi":
            c.enqueue("interaction", "rsi", args["message"], key=key)
            return ToolResult({"sent": True})
        if name == "update_brief":
            if c.state["status"] != "planned":
                raise ValueError("Hard constraints are frozen after start; discuss a new run")
            brief = decode(TaskBrief, json.loads(args["brief_json"]))
            run = c.store.run()
            run.brief_ref = c.store.put("briefs", key, brief)
            run.plan.goal = brief.objective
            run.plan.max_iterations = brief.limits.max_nodes
            run.plan.research_agent_count = brief.limits.max_agents
            c.store.save_run(run)
            if "intake" in c.state:
                config_path = c.store.root / "coordination" / "config.json"
                config = json.loads(config_path.read_text())
                config["brief"] = encode(brief)
                write_json(config_path, config)
                c.state["intake"].update(status="discussing", prepared_at=None)
                c.save()
            c.store.event("brief_updated", brief=encode(brief))
            return ToolResult({"brief": encode(brief)})
        if name == "save_evidence":
            refs = self.refs(agent_id, args["references"])
            actor = c.actor(agent_id)
            record = EvidenceRecord(
                effect_id("evidence", key),
                agent_id,
                args["title"],
                args["summary"],
                now(),
                actor.current_iteration_id,
                refs,
                False,
                args["limitations"],
            )
            path = c.store.root / "evidence" / f"{record.evidence_id}.json"
            if not path.exists():
                c.store.put("evidence", record.evidence_id, record)
            return ToolResult(
                {
                    "uri": c.store.paths.reference(path).uri,
                    "evidence_id": record.evidence_id,
                }
            )
        if name == "share_evidence":
            published = c.store.publish_evidence(
                args["evidence_id"],
                limitations=args["limitations"],
                publication_id=effect_id("evidence", key),
            )
            return ToolResult(self.evidence_public(published))
        if name == "save_candidate":
            candidate_id = effect_id("candidate", key)
            path = c.store.root / "candidates" / f"{candidate_id}.json"
            if path.exists():
                self.mark_workspace_candidate(agent_id, candidate_id)
                return ToolResult(
                    {
                        **encode(c.store.get("candidates", candidate_id, CandidateSnapshot)),
                        "uri": c.store.paths.reference(path).uri,
                    }
                )
            candidate = await c.store.snapshot(
                self.workspace(agent_id),
                agent_id=agent_id,
                description=args["description"],
                parent_candidate_id=self.workspace_candidate(agent_id),
                candidate_id=candidate_id,
                scope_base_candidate_id=c.state["seed_candidate_id"],
            )
            self.mark_workspace_candidate(agent_id, candidate_id)
            return ToolResult({**encode(candidate), "uri": c.store.paths.reference(path).uri})
        if name == "checkout_candidate":
            candidate = c.store.get("candidates", args["candidate_id"], CandidateSnapshot)
            await c.store.check_candidate_scope(candidate, c.state["seed_candidate_id"])
            await self.checkout_candidate(agent_id, candidate, key)
            return ToolResult({"candidate_id": candidate.candidate_id, "checked_out": True})
        if name == "finish":
            if any(a.state != AgentState.STOPPED for a in c.store.run().research_agents):
                raise ValueError("Stop research directions before concluding the run")
            run = c.store.run()
            contract = c.store.brief().verification
            selected = args.get("candidate_id") or None
            selected_ref = run.final_result_ref or run.best_result_ref
            if selected is None and selected_ref is not None:
                selected = c.store.public_record(selected_ref)["candidate_id"]
            if selected is not None:
                c.store.get("candidates", selected, CandidateSnapshot)
            require_final = self.require_final_confirmation()
            if require_final or (contract is not None and contract.target is not None):
                if contract is None or selected is None:
                    raise ValueError("No selected candidate has the required verification")
                phase = "final" if require_final else "search"
                results = [
                    result
                    for result in c.store.records("verifications", VerifiedResult)
                    if result.candidate_id == selected
                    and result.phase == phase
                    and result.contract_digest == c.store.contract_digest()
                    and result.simulated == c.state["simulated"]
                ]
                # A newer failed confirmation cannot be hidden by selecting an
                # older successful attempt of this same candidate.
                verified = max(results, key=lambda result: result.created_at) if results else None
                if (
                    verified is None
                    or verified.status != "succeeded"
                    or verified.passed is not True
                ):
                    raise ValueError(f"Selected candidate needs a successful {phase} verification")
                assert verified.score is not None
                if contract.target is not None and (
                    (contract.direction == "maximize" and verified.score < contract.target)
                    or (contract.direction == "minimize" and verified.score > contract.target)
                ):
                    raise ValueError(
                        f"Selected candidate's {phase} score has not reached the target"
                    )
                if require_final:
                    run.final_result_ref = c.store.paths.reference(
                        c.store.root / "verifications" / f"{verified.result_id}.json"
                    )
            c.state["selected_candidate_id"] = selected
            c.state["status"] = "succeeded"
            c.state["waiting_for_user"] = []
            c.save()
            run.state, run.finished_at = RunState.SUCCEEDED, now()
            c.store.save_run(run)
            c.enqueue("rsi", "user", args["summary"], key=key)
            c.store.event("run_completed", summary=args["summary"])
            return ToolResult({"completed": True}, stop=True, reason="completed")
        raise ValueError("Unknown tool")

    def evidence_public(self, record: EvidenceRecord, *, preview: bool = False) -> dict[str, Any]:
        result = {
            **encode(record),
            "uri": self.c.store.paths.reference(
                self.c.store.root / "evidence" / f"{record.evidence_id}.json"
            ).uri,
        }
        if preview:
            result.update(
                summary=record.summary[:1000],
                truncated=len(record.summary) > 1000,
                summary_chars=len(record.summary),
            )
        return result

    def require_final_confirmation(self) -> bool:
        contract = self.c.store.brief().verification
        return self.c.state.get("require_final_confirmation") is True or (
            contract is not None and contract.identity.get("require_final_confirmation") is True
        )

    def workspace_candidate(self, agent_id: str) -> str | None:
        marker = self.c.store.paths.agent(agent_id) / "workspace_initialized.json"
        return json.loads(marker.read_text()).get("candidate_id") if marker.exists() else None

    def mark_workspace_candidate(self, agent_id: str, candidate_id: str | None) -> None:
        write_json(
            self.c.store.paths.agent(agent_id) / "workspace_initialized.json",
            {"candidate_id": candidate_id},
        )

    async def checkout_candidate(
        self, agent_id: str, candidate: CandidateSnapshot, key: str
    ) -> None:
        parent = self.c.store.paths.agent(agent_id)
        workspace = self.workspace(agent_id)
        staging, backup = parent / (key + "-checkout"), parent / (key + "-previous")
        # If the process stopped between the two renames, retain/recover the
        # old workspace before checking whether it contains unsaved work.
        if backup.exists() and not any(workspace.iterdir()):
            workspace.rmdir()
            backup.rename(workspace)
        current = await self.c.store.content.snapshot(workspace)
        if current == candidate.content:
            self.mark_workspace_candidate(agent_id, candidate.candidate_id)
            if backup.exists():
                shutil.rmtree(backup)
            return
        source_id = self.workspace_candidate(agent_id)
        source = self.c.store.get("candidates", source_id, CandidateSnapshot) if source_id else None
        if (source is not None and source.content != current) or (
            source is None and any(workspace.iterdir())
        ):
            raise ValueError("Workspace has unsaved changes; save a candidate before checkout")
        if staging.exists():
            shutil.rmtree(staging)
        await self.c.store.content.materialize(candidate.content, staging)
        workspace.rename(backup)
        staging.rename(workspace)
        self.mark_workspace_candidate(agent_id, candidate.candidate_id)
        shutil.rmtree(backup)
        self.c.store.event(
            "candidate_checked_out", agent_id=agent_id, candidate_id=candidate.candidate_id
        )

    async def initialize_workspace(self, agent_id: str) -> None:
        root = self.workspace(agent_id)
        marker = self.c.store.paths.agent(agent_id) / "workspace_initialized.json"
        seed_id = self.c.state["seed_candidate_id"]
        if marker.exists() or seed_id is None:
            return
        candidate = self.c.store.get("candidates", seed_id, CandidateSnapshot)
        manifest = await self.c.store.content.read_json(candidate.content)
        assert isinstance(manifest, dict) and isinstance(manifest["files"], dict)
        from areal_rsi.api.base import ContentRef

        for name, ref in manifest["files"].items():
            target = root / safe_relative(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            body = await self.c.store.content.read_bytes(decode(ContentRef, ref))
            if target.exists() and target.read_bytes() != body:
                raise ValueError("Workspace initialization conflicts with existing files")
            target.write_bytes(body)
        self.mark_workspace_candidate(agent_id, seed_id)
