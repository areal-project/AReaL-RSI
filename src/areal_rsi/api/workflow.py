"""ResearchAgent, workflow, and user-entry interfaces."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from areal_rsi.api.base import (
    ContentRef,
    EvaluationReport,
    EvolutionTarget,
    ExperimentPlan,
    ExperimentRun,
    ResearchChange,
    SolutionVersion,
    VerifierSpec,
)
from areal_rsi.api.modules import (
    ArtifactBuilder,
    Benchmark,
    ContentStore,
    Evaluation,
    EvidenceStore,
    HarnessBuilder,
    HarnessRuntime,
    Memory,
    TrainingBackend,
    TrajectoryStore,
    Verifier,
    VersionManager,
)


class ResearchAgent(Protocol):
    """One researcher; propose receives an EvidenceBundle content reference.

    Implementations choose the exported file grant and tool-reading policy.
    The report argument is host-side context, not a required model-message payload.
    """

    @property
    def agent_id(self) -> str: ...

    async def propose(
        self,
        goal: str,
        target: EvolutionTarget,
        current: SolutionVersion,
        report: EvaluationReport,
        evidence: ContentRef,
    ) -> ResearchChange: ...

    async def select(
        self,
        current_solution_id: str,
        candidates: Sequence[EvaluationReport],
    ) -> str | None: ...


class RsiAgent(Protocol):
    """User entry and outer-loop controller for parallel research lines."""

    async def chat(self, message: str) -> str | ExperimentPlan: ...

    async def prune(self, run: ExperimentRun) -> Sequence[str]:
        """Return ResearchAgent IDs that should stop exploring."""
        ...


@dataclass(slots=True)
class WorkflowContext:
    content_store: ContentStore
    memory: Memory
    trajectories: TrajectoryStore
    versions: VersionManager
    harness_runtime: HarnessRuntime
    rsi_agent: RsiAgent
    research_agents: tuple[ResearchAgent, ...]
    benchmark: Benchmark
    verifier: Verifier
    verifier_spec: VerifierSpec
    evaluation: Evaluation
    harness_builder: HarnessBuilder | None = None
    training_backend: TrainingBackend | None = None
    artifact_builder: ArtifactBuilder | None = None
    evidence_store: EvidenceStore | None = None


class RsiWorkflow(Protocol):
    @property
    def name(self) -> str: ...

    async def run(self, context: WorkflowContext, plan: ExperimentPlan) -> ExperimentRun: ...


class ExperimentService(Protocol):
    async def start(self, plan: ExperimentPlan) -> ExperimentRun: ...

    async def status(self, run_id: str) -> ExperimentRun | None: ...

    async def stop(self, run_id: str) -> ExperimentRun: ...

    async def resume(self, run_id: str) -> ExperimentRun: ...

    async def list(self) -> Sequence[ExperimentRun]: ...
