"""可选的产物 ResearchAgent；研究与选版仍遵循同一个 ResearchAgent 契约。"""

from __future__ import annotations

import difflib
import json
from pathlib import Path

from areal_rsi.agents.common import ask_json
from areal_rsi.agents.research.default import DefaultResearchAgent
from areal_rsi.api.base import (
    ArtifactChange,
    BenchmarkSplit,
    ContentRef,
    EvaluationReport,
    EvolutionTarget,
    MemoryRecord,
    ModelVersion,
    SolutionVersion,
)
from areal_rsi.api.modules import ConfiguredAgentRuntime
from areal_rsi.base.storage import (
    FileContentStore,
    FileMemory,
    FileVersionManager,
    decode,
    new_id,
    safe_relative,
    write_json,
)
from areal_rsi.prompts import load_prompt


class ArtifactResearchAgent(DefaultResearchAgent):
    """只提出指定产物的文件编辑；构建、评测及版本保存由 Workflow 完成。"""

    def __init__(
        self,
        agent_id: str,
        runtime: ConfiguredAgentRuntime,
        model: ModelVersion,
        content: FileContentStore,
        memory: FileMemory,
        versions: FileVersionManager,
        root: Path,
        *,
        artifact_name: str,
    ):
        if not isinstance(artifact_name, str) or not artifact_name.strip():
            raise ValueError("必须显式指定非空 artifact_name")
        super().__init__(agent_id, runtime, model, content, memory, versions, root)
        self.artifact_name = artifact_name

    async def propose(
        self,
        goal: str,
        target: EvolutionTarget,
        current: SolutionVersion,
        report: EvaluationReport,
        evidence: ContentRef,
    ) -> ArtifactChange:
        if target != EvolutionTarget.ARTIFACT or report.solution_id != current.solution_id:
            raise ValueError("ArtifactResearchAgent 只支持同一 Solution 的 Artifact 提案")
        if report.split != BenchmarkSplit.TRAIN:
            raise ValueError("ResearchAgent 只接收 TRAIN 证据")
        matches = [
            artifact for artifact in current.artifacts if artifact.name == self.artifact_name
        ]
        if len(matches) > 1:
            raise ValueError("一个 Solution 内的 Artifact 名称不能重复")
        base = matches[0] if matches else None
        # 在 Session 之前读取内容仓库的目录清单；工作副本从不作为 patch 基线。
        snapshot = json.loads(await self.content.read_text(base.content))["files"] if base else {}
        workspace = self.root.resolve() / new_id("artifact-research")
        workspace.mkdir(parents=True)
        if base:
            await self.content.materialize(base.content, workspace / "artifact")
        else:
            (workspace / "artifact").mkdir()

        await self._prepare_evidence(evidence, report, workspace)
        # 共享 Memory 可能含其他工作流的记录；只导出本产物的 TRAIN 提案经验。
        records = sorted(
            (
                record
                for record in self.memory.records.list(MemoryRecord)
                if {"artifact", "train", f"artifact:{self.artifact_name}"} <= set(record.tags)
                and "validate" not in record.tags
                and record.source.startswith("trajectory:")
            ),
            key=lambda record: (record.created_at, record.memory_id),
        )[-5:]
        write_json(workspace / "memory.json", [{"text": record.text[:2000]} for record in records])

        session = await self._session(
            load_prompt("agents/research/evolution_artifact.md"),
            workspace,
        )
        try:
            proposal = await ask_json(
                session,
                json.dumps(
                    {
                        "goal": goal,
                        "artifact_name": self.artifact_name,
                        "artifact": "artifact/",
                        "creating": base is None,
                        "evidence": "train/",
                        "memory": "memory.json",
                        "reading_rules": self.evidence_instructions,
                    },
                    ensure_ascii=False,
                ),
            )
        finally:
            self.last_trajectory = (await session.finish()).trajectory_id
        changes, reason = proposal.get("files"), proposal.get("reason")
        if (
            set(proposal) != {"files", "reason"}
            or not isinstance(changes, dict)
            or not 1 <= len(changes) <= 8
            or not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > 4000
        ):
            raise ValueError("提案必须包含 1 到 8 个文件编辑及非空 reason")
        patches: list[str] = []
        for name, new_text in changes.items():
            path = safe_relative(name)
            if (
                path.as_posix() != name
                or not path.parts
                or ".git" in path.parts
                or any(character in name for character in "\x00\r\n\t")
                or len(name) > 1024
                or not isinstance(new_text, str)
                or len(new_text) > 100_000
                or "\x00" in new_text
            ):
                raise ValueError("非法 Artifact 编辑")
            old_text = (
                await self.content.read_text(decode(ContentRef, snapshot[name]))
                if name in snapshot
                else ""
            )
            if name in snapshot and old_text == new_text:
                continue
            old_path = json.dumps(f"a/{name}", ensure_ascii=False)
            new_path = json.dumps(f"b/{name}", ensure_ascii=False)
            patches.append(f"diff --git {old_path} {new_path}\n")
            if name not in snapshot:
                patches.append("new file mode 100644\n")
            if name not in snapshot and not new_text:
                # 空文件没有文本 hunk，必须显式声明创建，不能被 difflib 静默丢弃。
                patches.append("index 0000000..e69de29\n")
                continue
            for line in difflib.unified_diff(
                old_text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile=f"a/{name}" if name in snapshot else "/dev/null",
                tofile=f"b/{name}",
            ):
                # 保留产物原始换行约定，生成 git apply 可读取的无末尾换行标记。
                patches.append(
                    line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                )
        if not patches:
            raise ValueError("ResearchAgent 没有提出实际修改")
        patch = await self.content.put_text("".join(patches))
        await self.memory.add(
            reason,
            source=f"trajectory:{self.last_trajectory}",
            tags=("artifact", "train", f"artifact:{self.artifact_name}"),
        )
        return ArtifactChange(self.artifact_name, patch, reason)
