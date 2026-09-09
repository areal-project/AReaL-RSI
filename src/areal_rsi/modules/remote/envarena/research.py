"""Research remote capability configurations using an independent local Agent runtime."""

from __future__ import annotations

import json

from areal_rsi.agents.common import ask_json
from areal_rsi.agents.research import DefaultResearchAgent
from areal_rsi.api.base import (
    BenchmarkSplit,
    ContentRef,
    EvaluationReport,
    EvolutionTarget,
    HarnessChange,
    MemoryRecord,
    ResearchChange,
    SolutionVersion,
)
from areal_rsi.base.storage import decode, encode, json_bytes, new_id, safe_relative, write_json
from areal_rsi.modules.remote.envarena.harness import (
    REMOTE_CHANGE_TYPE,
    REMOTE_HARNESS_TYPE,
    validate_change,
)


class ArenaResearchAgent(DefaultResearchAgent):
    allowed_changes: tuple[str, ...] = ()

    async def propose(
        self,
        goal: str,
        target: EvolutionTarget,
        current: SolutionVersion,
        report: EvaluationReport,
        trajectories: ContentRef,
    ) -> ResearchChange:
        if target != EvolutionTarget.HARNESS or report.solution_id != current.solution_id:
            raise ValueError("remote 研究只接受当前 Solution 的 Harness 提案")
        if report.split != BenchmarkSplit.TRAIN:
            raise ValueError("ResearchAgent 只接收 TRAIN 证据")
        if current.harness.content.media_type != REMOTE_HARNESS_TYPE:
            raise ValueError("remote 研究需要远端 Harness 配置快照")
        if not self.allowed_changes:
            raise ValueError("请配置远端 Runtime 已支持的 allowed_changes")
        workspace = self.root / new_id("remote-research")
        workspace.mkdir(parents=True)
        harness = await self.content.read_json(current.harness.content)
        write_json(workspace / "harness.json", harness)
        write_json(workspace / "report.json", report)
        manifest = json.loads(await self.content.read_text(trajectories))
        summaries = []
        for index, entry in enumerate(manifest["trajectories"]):
            trace = await self.content.read_json(decode(ContentRef, entry["content"]))
            write_json(workspace / f"train-{index}.json", trace)
            assert isinstance(trace, dict)
            arena = trace.get("arena")
            if not isinstance(arena, dict):
                arena = {}
            attachments = arena.get("artifact_refs", [])
            if isinstance(attachments, list):
                for attachment in attachments:
                    if not isinstance(attachment, dict):
                        continue
                    name = attachment.get("name")
                    if not isinstance(name, str):
                        continue
                    artifact_path = workspace.joinpath(
                        f"train-artifacts-{index}", str(safe_relative(name))
                    )
                    artifact_path.parent.mkdir(parents=True, exist_ok=True)
                    artifact_path.write_bytes(
                        await self.content.read_bytes(decode(ContentRef, attachment["content"]))
                    )
            turns = trace.get("turns")
            if not isinstance(turns, list):
                turns = []
            summaries.append(
                {
                    "file": f"train-{index}.json",
                    "trajectory_id": entry["trajectory_id"],
                    "artifact_directory": f"train-artifacts-{index}",
                    "evaluation": {k: arena.get(k) for k in ("status", "score", "feedback")},
                    "recent_turns": [
                        {"role": t.get("role"), "content": str(t.get("content", ""))[:1800]}
                        for t in turns[-6:]
                        if isinstance(t, dict) and t.get("role") in {"user", "assistant"}
                    ],
                }
            )
        write_json(workspace / "train-index.json", summaries)
        write_json(workspace / "memory.json", self.memory.records.list(MemoryRecord))
        session = await self._session(
            "You are a ResearchAgent improving the capability configuration of an existing "
            "remote Harness. The execution runtime is owned by EnvArena. Read harness.json, "
            "report.json and train-index.json, then inspect bounded TRAIN evidence. "
            "Propose one coherent reusable improvement. Never change runtime, model, dataset, "
            "reward, output contract, agent environment, resources or TTL. Never embed case "
            "answers or case identifiers. Do not generate or upload an agent runtime. "
            "Only use the explicitly allowed configuration components. "
            'Return JSON {"reason":"...","changes":{...}}. Changes are partial at the '
            "component level; skills/tools lists REPLACE the existing list, so keep existing "
            "refs when appropriate. system_prompt accepts complete text or {append: text}. "
            "Prefer a compact append or a small new Skill; do not repeat the full baseline. "
            "Keep the proposed change concise (usually under 200 words). "
            "Even with perfect TRAIN scores, propose a justified refinement without deliberately "
            "weakening the baseline. skills is a list of "
            "key@version references or {name,description,content} new Skill definitions; "
            "content is Markdown without frontmatter. tools is a list of key@version refs "
            "or {name,description,mcp_config} definitions. mcp is {mcpServers:{...}}. "
            "All tool commands and URLs must be usable from the remote sandbox; never use "
            "local paths or credentials. Explain the improvement using observed evidence; "
            "a score improvement is not required or guaranteed. Produce the final JSON "
            "after a few short inspection calls. Existing runtime-specific prompt semantics "
            "and output requirements must be preserved.",
            workspace,
        )
        try:
            proposal = await ask_json(
                session,
                json.dumps(
                    {
                        "goal": goal,
                        "allowed_changes": self.allowed_changes,
                        "report": encode(report),
                        "harness": "harness.json",
                        "train_index": "train-index.json",
                    },
                    ensure_ascii=False,
                ),
            )
        finally:
            self.last_trajectory = (await session.finish()).trajectory_id
        changes = validate_change(proposal.get("changes"), self.allowed_changes)
        reason = proposal.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("remote 提案必须说明原因")
        await self.memory.add(
            reason[:4000], source=f"trajectory:{self.last_trajectory}", tags=("remote-harness",)
        )
        return HarnessChange(
            await self.content.put_bytes(json_bytes(changes), media_type=REMOTE_CHANGE_TYPE),
            reason[:4000],
        )
