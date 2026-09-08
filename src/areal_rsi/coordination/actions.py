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
        "description": "Exact run:// record URI returned by a tool; never a file path or web URL.",
    },
    "description": "Use tool-returned run:// record URIs. Put workspace paths and web URLs "
    "in instruction text, or first archive the source with fetch_url/save_evidence. "
    "Use [] when there is no archived reference.",
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
        "Read current authoritative run state, budget and evidence previews. "
        "Use read_record with a returned URI when full evidence is needed.",
    ),
    definition("read_record", "Read an accessible research record by its run URI.", uri=STRING),
    definition(
        "read_candidate_file",
        "Read a text file from an immutable candidate without changing "
        "your workspace. Use the candidate_id and the relative file path.",
        candidate_id=STRING,
        path=STRING,
    ),
    definition("list_files", "List files in your own persistent workspace."),
    definition("read_file", "Read a UTF-8 file in your own workspace.", path=STRING),
    definition(
        "write_file",
        "Create or replace a UTF-8 file in your own workspace.",
        path=STRING,
        content=STRING,
    ),
    definition(
        "save_evidence",
        "Archive an observation or finding, with real sources and limitations.",
        title=STRING,
        summary=STRING,
        references=REFERENCES,
        limitations=STRING,
    ),
    definition(
        "save_candidate",
        "Freeze your current workspace as an optional product version, preserving its parent.",
        description=STRING,
    ),
    definition(
        "checkout_candidate",
        "Continue from a saved candidate in your own workspace. Refuses to overwrite "
        "unsaved changes: save them first. RSI can specify this candidate in an assignment.",
        candidate_id=STRING,
    ),
]
RSI = [
    definition(
        "update_plan",
        "Save a concise current research plan and reason for its changes.",
        plan=STRING,
        reason=STRING,
    ),
    definition(
        "spawn_researcher",
        "Create a direction and issue its first concrete assignment.",
        direction=STRING,
        instruction=STRING,
        completion_criteria=STRING,
        references=REFERENCES,
    ),
    definition(
        "assign",
        "Reply to a researcher's latest report and issue its next assignment.",
        agent_id=STRING,
        instruction=STRING,
        completion_criteria=STRING,
        reply_to_report_id=STRING,
        references=REFERENCES,
    ),
    definition(
        "stop_researcher",
        "Stop a direction. Active execution is cancelled by the engine.",
        agent_id=STRING,
        reason=STRING,
    ),
    definition(
        "share_evidence",
        "Publish evidence for all directions, preserving provenance.",
        evidence_id=STRING,
        limitations=STRING,
    ),
    definition(
        "wait", "End inference until a new message arrives; give the event you need.", reason=STRING
    ),
    definition(
        "ask_user",
        "Ask Interaction to clarify an unresolved user requirement, then wait.",
        question=STRING,
    ),
    definition(
        "finish",
        "Conclude after checking the selected deliverable and its required "
        "final confirmation. candidate_id may be empty to select the final/best result.",
        summary=STRING,
        candidate_id=STRING,
    ),
]
RESEARCH = [
    definition(
        "submit_report",
        "Submit the assignment report and immediately stop until RSI replies.",
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
        "Forward user instructions or answers as a natural-language message.",
        message=STRING,
    ),
    definition(
        "ask_user", "Present a concise question to the user, then end this turn.", question=STRING
    ),
    definition(
        "update_brief",
        "Before start, save a revised brief. Fields are JSON as text; preserve "
        "objective, instructions, limits and optional verification. Read get_state first.",
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
            if actor.state == AgentState.STOPPED and call.name != "submit_report":
                return ToolResult({"error": "Agent was stopped"}, stop=True, reason="stopped")
            key = effect_id("action", agent_id + ":" + call.call_id)
            action_path = self.c.store.root / "actions" / f"{key}.json"
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
        if name == "wait":
            c.wait(agent_id, args["reason"])
            return ToolResult({"waiting_for": args["reason"]}, stop=True, reason="message")
        if name == "ask_user":
            recipient = "user" if agent_id == "interaction" else "interaction"
            message_id = c.enqueue(agent_id, recipient, args["question"], key=key)
            if recipient == "user" and not any(
                q["message_id"] == message_id for q in c.state["waiting_for_user"]
            ):
                c.state["waiting_for_user"].append(
                    {"message_id": message_id, "question": args["question"]}
                )
            c.save()
            c.wait(agent_id, recipient)
            return ToolResult({"question_sent": True}, stop=True, reason="user")
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
