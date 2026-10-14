"""Role-scoped model tools; execution adapters register additional tools explicitly."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from areal_rsi.api.interfaces import ToolCall, ToolHandler, ToolResult
from areal_rsi.api.records import AgentState, RecordRef, ResearchRun
from areal_rsi.api.research import CandidateSnapshot, EvidenceRecord, VerifiedResult
from areal_rsi.infra.coordination.config import (
    config_from_dict,
    git_common_directory,
    git_repository,
)
from areal_rsi.infra.coordination.coordinator import Coordinator, effect_id
from areal_rsi.infra.coordination.tools.brief import brief_tool, updated_brief
from areal_rsi.infra.coordination.views import model_state, record_preview
from areal_rsi.infra.storage.files import decode, encode, now, safe_name, safe_relative, write_json

STRING = {"type": "string"}
RECORD_CATEGORIES = (
    "evidence",
    "reports",
    "assignments",
    "candidates",
    "withdrawals",
    "verifications",
    "executions",
    "materials",
)
REFERENCES = {
    "type": "array",
    "items": {
        "type": "string",
        "description": "精确记录 ID、公开类别/记录ID 或 run:// URI，"
        "不接受工作区文件路径或网页地址。",
    },
    "description": "优先填写工具返回的记录 ID，无需重写 run ID；"
    "也接受公开类别/记录ID、完整 run:// URI。"
    "只解析本 run 的精确记录，不猜测拼写。文件路径和网页地址写在正文中，"
    "或先用 save_evidence 归档发现后引用。没有记录引用时使用 []。",
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
    definition(
        "read_record",
        "用工具返回的记录 ID 或 run:// URI 读取你有权限查看的本 run 研究记录。",
        uri=STRING,
    ),
    definition(
        "list_records",
        "按时间倒序查找历史记录，每页 10 条；旧作业材料缺少时间时按来源作业时间排序。"
        "返回摘要和引用，详情用 read_record 读取。",
        category={
            "type": "string",
            "enum": list(RECORD_CATEGORIES),
        },
        offset={"type": "integer", "minimum": 0},
    ),
    definition(
        "read_candidate_file",
        "读取已保存产物中的文本文件，不切换工作区。path 为空时列出实际文件路径。",
        candidate_id=STRING,
        path=STRING,
    ),
    definition(
        "import_candidate",
        "将本 run 的不可变产物完整复制到工作区的新子目录，支持任意文件类型。"
        "用于组合、比较和继续研究，无需通过模型重写文件；不覆盖已有修改。",
        candidate_id=STRING,
        destination=STRING,
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
        "将本 Agent 的默认工作区保存为不可变候选，记录父版本。"
        "bash 的 workdir 不会改变保存目录；请核对回执中的 workspace 和文件数量。"
        "也可保存供同伴复用的中间产物；保存不代表验证通过。仅在需要保留产物时使用。",
        description=STRING,
    ),
    definition(
        "checkout_candidate",
        "从已保存的候选继续工作。若工作区还有未保存的修改，先保存快照；工具不会覆盖这些修改。",
        candidate_id=STRING,
    ),
    definition(
        "withdraw_candidate",
        "发现具体反例或违反任务契约后，将该产物及内容完全相同的快照从选优和交付中撤回。"
        "研究员可直接撤回本 run 中的产物，系统自动通知 RSI。保留原产物、历史分数和证据；"
        "修复后保存新产物再验证。仅有待查疑问或速度较慢时不撤回。",
        candidate_id=STRING,
        reason=STRING,
        references=REFERENCES,
    ),
]
RSI = [
    definition("wait", "等待研究员报告。", reason=STRING),
    definition("ask_user", "说明需要用户补充的信息并等待。", question=STRING),
]
RESEARCH: list[dict[str, Any]] = []
INTERACTION = [definition("ask_user", "向用户澄清需求并等待。", question=STRING)]


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

    def definitions(self, agent_id: str, *, include_hidden: bool = False) -> list[dict[str, Any]]:
        role = self.role(agent_id)
        common = (
            COMMON
            if role != "interaction"
            else [
                tool
                for tool in COMMON
                if tool["name"]
                in {
                    "get_state",
                    "read_record",
                    "list_records",
                    "read_candidate_file",
                    "import_candidate",
                }
            ]
        )
        hidden = set()
        if not include_hidden:
            if {"read", "write", "glob"} <= self.extras.keys():
                hidden.update({"read_file", "write_file", "list_files"})
            if self.c.state.status != "planned":
                hidden.update({"get_setup", "start_research", "update_brief"})
        return [
            tool
            for tool in [
                *common,
                *([brief_tool(self.c.state.mode or "goal")] if role == "interaction" else []),
                *{"rsi": RSI, "interaction": INTERACTION, "research": RESEARCH}[role],
                *(tool for tool, _, roles in self.extras.values() if role in roles),
            ]
            if tool["name"] not in hidden
        ]

    def handler(self, agent_id: str) -> ToolHandler:
        async def handle(call: ToolCall) -> ToolResult:
            allowed = {tool["name"] for tool in self.definitions(agent_id, include_hidden=True)}
            if call.name not in allowed:
                return ToolResult({"error": "Tool is not available to this role"})
            actor = self.c.actor(agent_id)
            key = effect_id("action", agent_id + ":" + call.call_id)
            action_path = self.c.store.root / "actions" / f"{key}.json"
            if (
                self.c.state.status != "planned"
                and call.name in {"start_research", "update_brief"}
                and not action_path.exists()
            ):
                return ToolResult({"error": "Tool is not available after research starts"})
            if actor.state == AgentState.STOPPED and call.name != "submit_report":
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

    def _worktree_path(self, agent_id: str) -> Path:
        return self.c.store.root.parent.parent / "worktrees" / self.c.store.run_id / agent_id

    def workspace(self, agent_id: str) -> Path:
        if self.c.state.workspace_dir and (
            agent_id != "interaction" and (agent_id != "rsi" or self.c.state.mode == "goal")
        ):
            return self._worktree_path(agent_id)
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
        uri = self.c.store.record_reference(RecordRef(uri)).uri
        value = self.c.store.public_record(RecordRef(uri))
        if self.role(agent_id) in {"rsi", "interaction"}:
            return value
        category = urlsplit(uri).path.split("/")[1]
        if category in {"briefs", "candidates", "verifications", "withdrawals"}:
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
        refs = []
        for uri in values:
            if not uri.strip():
                continue
            ref = self.c.store.record_reference(RecordRef(uri.strip()))
            self.read_record(agent_id, ref.uri)
            if ref not in refs:
                refs.append(ref)
        return tuple(refs)

    async def _apply(
        self,
        agent_id: str,
        key: str,
        name: str,
        args: dict[str, Any],
    ) -> ToolResult:
        c = self.c
        if name == "withdraw_candidate":
            ref = c.store.withdraw_candidate(
                args["candidate_id"],
                args["reason"],
                self.refs(agent_id, args["references"]),
                simulated_run=c.state.simulated,
                agent_id=agent_id,
            )
            selected = c.selected_candidate_id
            if selected and c.store.is_withdrawn(selected):
                c.select_candidate(None)
                c.save()
            withdrawal = c.store.public_record(ref)
            if self.role(agent_id) == "research" and withdrawal["agent_id"] == agent_id:
                # The withdrawal is durable before notification. Replaying this
                # action repairs delivery without duplicating the message.
                c.enqueue(
                    agent_id,
                    "rsi",
                    "研究员已因具体反例撤回产物："
                    + withdrawal["candidate_id"]
                    + "\n原因："
                    + withdrawal["reason"]
                    + "\n撤回已生效；原产物、历史分数和证据仍保留。\n记录："
                    + ref.uri,
                    key="withdrawal:" + withdrawal["candidate_id"],
                    references=(
                        ref,
                        *(RecordRef(item["uri"]) for item in withdrawal["references"]),
                    ),
                )
            return ToolResult({"withdrawn": True, "uri": ref.uri, "best": c.summary()["best"]})
        if name == "get_state":
            summary = model_state(c, agent_id)
            if agent_id == "interaction":
                ids = c.current_message_ids(agent_id)
                summary["current_message_id"] = ids[-1] if ids else None
                summary["current_message_ids"] = ids
            summary["workspace_candidate_id"] = self.workspace_candidate(agent_id)
            summary["require_final_confirmation"] = self.require_final_confirmation()
            return ToolResult(summary)
        if name == "list_records":
            category, offset = args["category"], args["offset"]
            if category not in RECORD_CATEGORIES:
                raise ValueError("Unknown public record category")
            if type(offset) is not int or offset < 0:
                raise ValueError("Offset must be a nonnegative integer")
            records = []
            for path in (c.store.root / category).glob("*.json"):
                ref = c.store.paths.reference(path)
                try:
                    value = self.read_record(agent_id, ref.uri)
                except ValueError:
                    continue
                source_job = None
                if category == "materials" and isinstance(value.get("job_id"), str):
                    try:
                        job_id = safe_name(value["job_id"])
                        source_job = self.read_record(
                            agent_id, f"run://{c.store.run_id}/executions/{job_id}.json"
                        )
                    except (ValueError, FileNotFoundError):
                        pass  # Do not infer missing or unauthorized job metadata.
                records.append(record_preview(value, ref, source_job=source_job))
            records.sort(
                key=lambda item: (
                    item.get("created_at") or item.get("job_created_at") or "",
                    item["uri"],
                ),
                reverse=True,
            )
            page = records[offset : offset + 10]
            return ToolResult(
                {
                    "records": page,
                    "total": len(records),
                    "next_offset": offset + 10 if offset + 10 < len(records) else None,
                }
            )
        if name == "read_record":
            return ToolResult(self.read_record(agent_id, args["uri"]))
        if name == "read_candidate_file":
            candidate = c.store.get("candidates", args["candidate_id"], CandidateSnapshot)
            manifest = await c.store.content.read_json(candidate.content)
            assert isinstance(manifest, dict) and isinstance(manifest["files"], dict)
            if not args["path"] or args["path"] not in manifest["files"]:
                files = sorted(manifest["files"])
                return ToolResult(
                    {
                        "candidate_id": candidate.candidate_id,
                        "files": files[:1000],
                        "truncated": len(files) > 1000,
                        "notice": "请选择产物中的实际路径" if args["path"] else "",
                    }
                )
            relative_name = safe_relative(args["path"]).as_posix()
            from areal_rsi.api.records import ContentRef

            ref = decode(ContentRef, manifest["files"][relative_name])
            text = await c.store.content.read_text(ref)
            if len(text) > 256000:
                raise ValueError("Candidate file is too large for the model context")
            return ToolResult({"path": relative_name, "content": text})
        if name == "import_candidate":
            candidate = c.store.get("candidates", args["candidate_id"], CandidateSnapshot)
            await c.store.check_candidate_scope(candidate, c.state.seed_candidate_id)
            target = self.workspace_path(agent_id, args["destination"])
            if target.exists():
                if (
                    not target.is_dir()
                    or await c.store.content.snapshot(target) != candidate.content
                ):
                    raise ValueError("Destination contains different files; choose a new directory")
            else:
                staging = c.store.paths.agent(agent_id) / (key + "-import")
                if staging.exists():
                    shutil.rmtree(staging)
                await c.store.content.materialize(candidate.content, staging)
                target.parent.mkdir(parents=True, exist_ok=True)
                staging.rename(target)
            c.link_reference(
                agent_id,
                c.store.paths.reference(
                    c.store.root / "candidates" / f"{candidate.candidate_id}.json"
                ),
            )
            return ToolResult({"candidate_id": candidate.candidate_id, "directory": str(target)})
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
        if name == "wait":
            c.wait(agent_id, args["reason"])
            return ToolResult({"waiting_for": args["reason"]}, stop=True, reason="message")
        if name == "ask_user":
            message_id = c.ask_user(agent_id, args["question"], key=key)
            return ToolResult(
                {"question_sent": True, "message_id": message_id}, stop=True, reason="user"
            )
        if name == "update_brief":
            if c.state.status != "planned" or c.state.intake.status == "starting":
                raise ValueError("Hard constraints are frozen after start; discuss a new run")
            brief = updated_brief(c.state.mode or "goal", c.store.brief(), args)
            config_path = c.store.root / "coordination" / "config.json"
            config = None
            if c.state.intake.status is not None:
                config = json.loads(config_path.read_text())
                config["brief"] = encode(brief)
                # Validate the combined configuration before changing any durable record.
                config_from_dict(config)
            run = c.store.run()
            run.brief_ref = c.store.put("briefs", key, brief)
            run.plan.goal = brief.objective
            run.plan.max_iterations = brief.limits.max_nodes
            run.plan.research_agent_count = brief.limits.max_agents
            c.store.save_run(run)
            if config is not None:
                write_json(config_path, config)
                c.state.intake.status, c.state.intake.prepared_at = "discussing", None
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
            c.link_reference(agent_id, c.store.paths.reference(path))
            return ToolResult(
                {
                    "uri": c.store.paths.reference(path).uri,
                    "evidence_id": record.evidence_id,
                }
            )
        if name == "save_candidate":
            candidate_id = effect_id("candidate", key)
            path = c.store.root / "candidates" / f"{candidate_id}.json"
            if path.exists():
                c.link_reference(agent_id, c.store.paths.reference(path))
                self.mark_workspace_candidate(agent_id, candidate_id)
                return ToolResult(
                    await self.candidate_receipt(
                        agent_id, c.store.get("candidates", candidate_id, CandidateSnapshot)
                    )
                )
            candidate = await c.store.snapshot(
                self.workspace(agent_id),
                agent_id=agent_id,
                description=args["description"],
                parent_candidate_id=self.workspace_candidate(agent_id),
                candidate_id=candidate_id,
                scope_base_candidate_id=c.state.seed_candidate_id,
            )
            self.mark_workspace_candidate(agent_id, candidate_id)
            c.link_reference(agent_id, c.store.paths.reference(path))
            return ToolResult(await self.candidate_receipt(agent_id, candidate))
        if name == "checkout_candidate":
            candidate = c.store.get("candidates", args["candidate_id"], CandidateSnapshot)
            await c.store.check_candidate_scope(candidate, c.state.seed_candidate_id)
            await self.checkout_candidate(agent_id, candidate, key)
            return ToolResult({"candidate_id": candidate.candidate_id, "checked_out": True})
        raise ValueError("Unknown tool")

    def completion_candidate(
        self,
        selected: str | None,
        *,
        final_confirmation: bool = True,
        require_verification: bool = False,
    ) -> tuple[ResearchRun, str | None]:
        c = self.c
        run = c.store.run()
        contract = c.store.brief().verification
        selected_ref = run.final_result_ref or run.best_result_ref
        if selected is None and selected_ref is not None:
            selected = c.store.public_record(selected_ref)["candidate_id"]
        if selected is not None:
            c.store.get("candidates", selected, CandidateSnapshot)
            if c.store.is_withdrawn(selected):
                raise ValueError(
                    "Selected candidate was withdrawn; repair and verify a new artifact"
                )
        require_final = self.require_final_confirmation() if final_confirmation else False
        if require_final or (
            contract is not None and (require_verification or contract.target is not None)
        ):
            if contract is None or selected is None:
                raise ValueError("No selected candidate has the required verification")
            phase = "final" if require_final else "search"
            results = [
                result
                for result in c.store.records("verifications", VerifiedResult)
                if result.candidate_id == selected
                and result.phase == phase
                and result.contract_digest == c.store.contract_digest()
                and result.goal_revision == c.store.brief().revision
                and result.simulated == c.state.simulated
            ]
            # A newer failed confirmation cannot be hidden by selecting an
            # older successful attempt of this same candidate.
            verified = max(results, key=lambda result: result.created_at) if results else None
            if verified is None or verified.status != "succeeded" or verified.passed is not True:
                raise ValueError(f"Selected candidate needs a successful {phase} verification")
            assert verified.score is not None
            if contract.target is not None and (
                (contract.direction == "maximize" and verified.score < contract.target)
                or (contract.direction == "minimize" and verified.score > contract.target)
            ):
                raise ValueError(f"Selected candidate's {phase} score has not reached the target")
            if require_final:
                run.final_result_ref = c.store.paths.reference(
                    c.store.root / "verifications" / f"{verified.result_id}.json"
                )
        return run, selected

    async def candidate_receipt(
        self, agent_id: str, candidate: CandidateSnapshot
    ) -> dict[str, Any]:
        manifest = await self.c.store.content.read_json(candidate.content)
        assert isinstance(manifest, dict) and isinstance(manifest["files"], dict)
        files = sorted(manifest["files"])
        return {
            **encode(candidate),
            "uri": self.c.store.paths.reference(
                self.c.store.root / "candidates" / f"{candidate.candidate_id}.json"
            ).uri,
            "workspace": str(self.workspace(agent_id)),
            "file_count": len(files),
            "files": files[:20],
            "files_truncated": len(files) > 20,
            "notice": (
                "此快照没有文件。若预期保存代码或产物，请将文件放入上述 workspace 后重新保存；"
                "bash 使用的其他目录不会自动纳入候选。"
                if not files
                else ""
            ),
        }

    def evidence_public(self, record: EvidenceRecord) -> dict[str, Any]:
        result = {
            **encode(record),
            "uri": self.c.store.paths.reference(
                self.c.store.root / "evidence" / f"{record.evidence_id}.json"
            ).uri,
        }
        return result

    def require_final_confirmation(self) -> bool:
        contract = self.c.store.brief().verification
        return self.c.state.require_final_confirmation is True or (
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
        if self.c.state.workspace_dir and (
            agent_id != "interaction" and (agent_id != "rsi" or self.c.state.mode == "goal")
        ):
            # A linked worktree must keep its .git pointer and directory identity.
            backup.mkdir()
            try:
                for path in workspace.iterdir():
                    if path.name != ".git":
                        path.rename(backup / path.name)
                for path in staging.iterdir():
                    if path.name == ".git":
                        raise ValueError("候选内容不能覆盖 Git worktree 元数据")
                    path.rename(workspace / path.name)
            except BaseException:
                for path in workspace.iterdir():
                    if path.name != ".git":
                        if path.is_dir():
                            shutil.rmtree(path)
                        else:
                            path.unlink()
                for path in backup.iterdir():
                    path.rename(workspace / path.name)
                raise
            finally:
                shutil.rmtree(staging)
                shutil.rmtree(backup)
            self.mark_workspace_candidate(agent_id, candidate.candidate_id)
            self.c.store.event(
                "candidate_checked_out",
                agent_id=agent_id,
                candidate_id=candidate.candidate_id,
            )
            return
        workspace.rename(backup)
        staging.rename(workspace)
        self.mark_workspace_candidate(agent_id, candidate.candidate_id)
        shutil.rmtree(backup)
        self.c.store.event(
            "candidate_checked_out", agent_id=agent_id, candidate_id=candidate.candidate_id
        )

    async def initialize_workspace(self, agent_id: str) -> None:
        if self.c.state.workspace_dir and (
            agent_id != "interaction" and (agent_id != "rsi" or self.c.state.mode == "goal")
        ):
            source = git_repository(self.c.state.workspace_dir)
            target = self._worktree_path(agent_id)
            if target.exists():
                if git_repository(target) != target.resolve() or git_common_directory(
                    target
                ) != git_common_directory(source):
                    raise ValueError("现有工作目录不属于所选 Git 仓库：" + str(target))
                return
            base = self.c.state.workspace_base_commit
            if not base:
                raise ValueError("研究启动前缺少 Git 基线提交")
            branch = f"rsi/{self.c.store.run_id}/{agent_id}"
            target.parent.mkdir(parents=True, exist_ok=True)
            exists = (
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(source),
                        "show-ref",
                        "--verify",
                        "--quiet",
                        "refs/heads/" + branch,
                    ],
                    check=False,
                ).returncode
                == 0
            )
            command = ["git", "-C", str(source), "worktree", "add"]
            command += [str(target), branch] if exists else ["-b", branch, str(target), base]
            result = await asyncio.to_thread(
                subprocess.run, command, capture_output=True, text=True, check=False
            )
            if result.returncode:
                raise ValueError("创建 Git worktree 失败：" + result.stderr.strip())
            self.c.store.event(
                "worktree_created",
                agent_id=agent_id,
                path=str(target),
                branch=branch,
                base_commit=base,
            )
            return
        root = self.workspace(agent_id)
        marker = self.c.store.paths.agent(agent_id) / "workspace_initialized.json"
        seed_id = self.c.state.seed_candidate_id
        if marker.exists() or seed_id is None:
            return
        candidate = self.c.store.get("candidates", seed_id, CandidateSnapshot)
        manifest = await self.c.store.content.read_json(candidate.content)
        assert isinstance(manifest, dict) and isinstance(manifest["files"], dict)
        from areal_rsi.api.records import ContentRef

        for name, ref in manifest["files"].items():
            target = root / safe_relative(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            body = await self.c.store.content.read_bytes(decode(ContentRef, ref))
            if target.exists() and target.read_bytes() != body:
                raise ValueError("Workspace initialization conflicts with existing files")
            target.write_bytes(body)
        self.mark_workspace_candidate(agent_id, seed_id)
