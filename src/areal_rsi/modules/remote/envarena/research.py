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
from areal_rsi.base.storage import json_bytes, new_id, write_json
from areal_rsi.modules.remote.envarena.harness import (
    REMOTE_CHANGE_TYPE,
    REMOTE_HARNESS_TYPE,
    validate_change,
)
from areal_rsi.prompts import load_prompt


class ArenaResearchAgent(DefaultResearchAgent):
    allowed_changes: tuple[str, ...] = ()

    async def propose(
        self,
        goal: str,
        target: EvolutionTarget,
        current: SolutionVersion,
        report: EvaluationReport,
        evidence: ContentRef,
    ) -> ResearchChange:
        if target != EvolutionTarget.HARNESS or report.solution_id != current.solution_id:
            raise ValueError("remote 研究只接受当前 Solution 的 Harness 提案")
        if report.split != BenchmarkSplit.TRAIN:
            raise ValueError("ResearchAgent 只接收 TRAIN 证据")
        if current.harness.content.media_type != REMOTE_HARNESS_TYPE:
            raise ValueError("remote 研究需要远端 Harness 配置快照")
        if not self.allowed_changes:
            raise ValueError("请配置远端 Runtime 已支持的 allowed_changes")
        workspace = self.root.resolve() / new_id("remote-research")
        workspace.mkdir(parents=True)
        harness = await self.content.read_json(current.harness.content)
        write_json(workspace / "harness.json", harness)
        # Reuse the common grant, integrity checks and file index for TRAIN evidence.
        await self._prepare_evidence(evidence, report, workspace)
        write_json(workspace / "memory.json", self.memory.records.list(MemoryRecord))
        session = await self._session(
            load_prompt("agents/research/evolution_remote.md"),
            workspace,
        )
        try:
            proposal = await ask_json(
                session,
                json.dumps(
                    {
                        "goal": goal,
                        "allowed_changes": self.allowed_changes,
                        "harness": "harness.json",
                        "evidence": "train/index.json",
                        "evidence_instructions": self.evidence_instructions,
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
