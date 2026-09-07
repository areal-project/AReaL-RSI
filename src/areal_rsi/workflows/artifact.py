"""可选的用户产物自迭代：显式注册后启用，评测由注入的用户模块负责。"""

from __future__ import annotations

from areal_rsi.api.base import (
    ArtifactChange,
    ArtifactVersion,
    EvolutionTarget,
    ExperimentPlan,
    ResearchChange,
    SolutionVersion,
)
from areal_rsi.api.workflow import WorkflowContext
from areal_rsi.workflows.evolution import HarnessEvolutionWorkflow


class ArtifactEvolutionWorkflow(HarnessEvolutionWorkflow):
    """每轮创建或修改一个具名 Artifact，沿用多线、选版、剪枝和恢复机制。"""

    name = "artifact-evolution"
    _change_type = ArtifactChange

    def _validate_context(self, context: WorkflowContext, plan: ExperimentPlan) -> None:
        if plan.target != EvolutionTarget.ARTIFACT or context.artifact_builder is None:
            raise ValueError("Artifact 工作流要求 ARTIFACT 目标和 artifact_builder")

    @staticmethod
    def _artifacts(solution: SolutionVersion) -> dict[str, ArtifactVersion]:
        artifacts = {artifact.name: artifact for artifact in solution.artifacts}
        if len(artifacts) != len(solution.artifacts) or any(not name.strip() for name in artifacts):
            raise ValueError("Artifact 名称必须非空且不能重复")
        return artifacts

    async def _build_candidate(
        self, ctx: WorkflowContext, current: SolutionVersion, change: ResearchChange
    ) -> SolutionVersion:
        assert ctx.artifact_builder is not None and isinstance(change, ArtifactChange)
        if not change.artifact_name.strip():
            raise ValueError("Artifact 修改必须指定非空产物名称")
        base = self._artifacts(current).get(change.artifact_name)
        content = await ctx.artifact_builder.build(base, change)
        artifact = await ctx.versions.create_artifact(
            change.artifact_name,
            content,
            parent_version_id=base.version_id if base else None,
        )
        # 保持其他产物的版本及顺序，新产物追加到末尾。
        artifacts = tuple(
            artifact if item.name == change.artifact_name else item for item in current.artifacts
        )
        if base is None:
            artifacts = (*artifacts, artifact)
        return await ctx.versions.create_solution(
            current.model,
            current.harness,
            artifacts=artifacts,
            parent_solution_id=current.solution_id,
        )

    def _validate_candidate(
        self,
        candidate: SolutionVersion,
        current: SolutionVersion,
        baseline: SolutionVersion,
        change: ResearchChange,
    ) -> None:
        assert isinstance(change, ArtifactChange)
        if (
            current.model != baseline.model
            or current.harness != baseline.harness
            or candidate.model != baseline.model
            or candidate.harness != baseline.harness
            or candidate.parent_solution_id != current.solution_id
            or candidate.solution_id == current.solution_id
        ):
            raise ValueError("Artifact 实验禁止改变 Model/Harness 或丢失 Solution 父版本关系")
        before, after = self._artifacts(current), self._artifacts(candidate)
        base, artifact = before.get(change.artifact_name), after.get(change.artifact_name)
        if artifact is None or artifact.parent_version_id != (base.version_id if base else None):
            raise ValueError("Artifact 候选缺少目标产物或父版本关系错误")
        if base and (
            artifact.version_id == base.version_id or artifact.content.digest == base.content.digest
        ):
            raise ValueError("Artifact 候选必须创建有内容变化的新版本")
        expected = tuple(
            artifact if item.name == change.artifact_name else item for item in current.artifacts
        )
        if base is None:
            expected = (*expected, artifact)
        if candidate.artifacts != expected:
            raise ValueError("Artifact 修改不能改变其他产物的版本或顺序")
