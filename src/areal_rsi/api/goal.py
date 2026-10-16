"""Versioned production goals. Execution completion never implies acceptance."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum

from areal_rsi.api.base import (
    ContentRef,
    EvaluationReport,
    EvolutionTarget,
    JsonObject,
    JsonValue,  # noqa: F401
)


class GoalState(StrEnum):
    DRAFT = "draft"
    NEEDS_INPUT = "needs_input"
    READY = "ready"
    RUNNING = "running"
    ACHIEVED = "achieved"
    BUDGET_EXHAUSTED = "budget_exhausted"
    BLOCKED = "blocked"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class MetricRequirement:
    metric: str
    threshold: float
    direction: str = "gte"

    def __post_init__(self) -> None:
        if not self.metric.strip() or self.direction not in {"gte", "lte"}:
            raise ValueError("Metric requires a name and gte/lte direction")
        if not math.isfinite(self.threshold):
            raise ValueError("Metric threshold must be finite")


@dataclass(slots=True)
class AcceptanceSpec:
    metrics: tuple[MetricRequirement, ...]
    verifier_version: str
    required_checks: tuple[str, ...] = ()
    final_protocol: str = "independent-holdout-v1"

    def __post_init__(self) -> None:
        if not self.metrics or not self.verifier_version.strip():
            raise ValueError("Acceptance requires metrics and a verifier version")
        if self.final_protocol != "independent-holdout-v1":
            raise ValueError("Unsupported final evaluation protocol")
        if len({m.metric for m in self.metrics}) != len(self.metrics):
            raise ValueError("Duplicate acceptance metric")


@dataclass(slots=True)
class BudgetSpec:
    max_iterations: int
    max_seconds: float
    max_retries: int = 1

    def __post_init__(self) -> None:
        if type(self.max_iterations) is not int or not 1 <= self.max_iterations <= 100:
            raise ValueError("max_iterations must be an integer in [1,100]")
        if (
            type(self.max_seconds) not in (int, float)
            or not math.isfinite(self.max_seconds)
            or self.max_seconds <= 0
        ):
            raise ValueError("max_seconds must be positive and finite")
        if type(self.max_retries) is not int or not 0 <= self.max_retries <= 10:
            raise ValueError("max_retries must be an integer in [0,10]")


@dataclass(slots=True)
class GoalSpec:
    description: str
    target: EvolutionTarget
    acceptance: AcceptanceSpec
    budget: BudgetSpec
    allowed_paths: tuple[str, ...]
    invariants: tuple[str, ...]
    benchmark: str = "arithmetic-v1"
    artifact_path: str | None = None
    artifact_name: str = "app"
    # None inherits deployment defaults; () explicitly selects no research Skills.
    skill_paths: tuple[str, ...] | None = None
    research_agent_count: int = 1
    assets: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.target = EvolutionTarget(self.target)
        if not self.description.strip() or not self.allowed_paths or not self.invariants:
            raise ValueError("Goal requires description, allowed_paths and invariants")
        if any(
            not isinstance(p, str)
            or not p.strip()
            or p.startswith("/")
            or ".." in p.split("/")
            or "\\" in p
            for p in self.allowed_paths
        ):
            raise ValueError("allowed_paths must contain relative path patterns within the target")
        if self.target == EvolutionTarget.ARTIFACT and not self.artifact_path:
            raise ValueError("Artifact goal requires artifact_path")
        if type(self.research_agent_count) is not int or not 1 <= self.research_agent_count <= 8:
            raise ValueError("research_agent_count must be in [1,8]")


@dataclass(slots=True)
class SkillVersion:
    skill_id: str
    version: str
    description: str
    scope: str
    dependencies: tuple[str, ...]
    content: ContentRef
    entrypoint: str = "SKILL.md"


@dataclass(slots=True)
class ResearcherSpec:
    model_identity: JsonObject
    runtime_digest: str
    skills: tuple[SkillVersion, ...] = ()
    tools: tuple[str, ...] = ("bash@v1",)


@dataclass(slots=True)
class GoalRevision:
    goal_id: str
    revision: int
    state: GoalState
    request: JsonObject
    created_at: str
    questions: tuple[str, ...] = ()
    clarification: tuple[JsonObject, ...] = ()
    spec: GoalSpec | None = None
    confirmed_at: str | None = None
    confirmation_source: str | None = None
    frozen_identity: JsonObject = field(default_factory=dict)
    researcher: ResearcherSpec | None = None
    run_id: str | None = None


@dataclass(slots=True)
class GoalOutcome:
    goal_id: str
    revision: int
    state: GoalState
    achieved: bool
    stop_reason: str
    solution_id: str | None
    acceptance_report: ContentRef | None
    elapsed_seconds: float
    attempts: int
    token_usage: JsonObject | None = None
    cost: float | None = None
    human_seconds: float | None = None
    cancellation_grace_seconds: float = 0.0


def comparison_metrics(before: EvaluationReport, after: EvaluationReport) -> dict[str, float]:
    baseline = {**before.metrics, "score": before.score}
    candidate = {**after.metrics, "score": after.score}
    return {
        **candidate,
        **{
            "gain_" + key: value - baseline[key]
            for key, value in candidate.items()
            if key in baseline and math.isfinite(value) and math.isfinite(baseline[key])
        },
    }


def accepts(spec: AcceptanceSpec, metrics: dict[str, float], *, final: bool = False) -> bool:
    """Fail closed; final=True also requires complete comparative evidence."""
    requirements = [m for m in spec.metrics if final or not m.metric.startswith("gain_")]
    for item in requirements:
        value = metrics.get(item.metric)
        if value is None or not math.isfinite(value):
            return False
        # Subtracting two measured floats can put an exact mathematical boundary
        # a few representable values below the threshold (for example 1 - .9).
        at_boundary = abs(value - item.threshold) <= 8 * max(
            math.ulp(value), math.ulp(item.threshold)
        )
        if item.direction == "gte" and value < item.threshold and not at_boundary:
            return False
        if item.direction == "lte" and value > item.threshold and not at_boundary:
            return False
    return all(metrics.get(check) == 1.0 for check in spec.required_checks)


@dataclass(slots=True)
class IterationEvidence:
    """Portable projection linking one development step to its frozen identities."""

    goal_id: str
    revision: int
    run_id: str
    iteration: int
    agent_id: str
    input_solution_id: str
    candidate_solution_id: str
    next_solution_id: str | None
    change: ContentRef
    verifier_digest: str | None
    selection_reason: str
    skill_versions: tuple[SkillVersion, ...]
    started_at: str
    finished_at: str
