"""Small, serializable base objects used by the public RSI API."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeAlias

JsonPrimitive: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]

TRAJECTORY_BUNDLE_MEDIA_TYPE = "application/vnd.areal-rsi.trajectories+json"


class EvolutionTarget(StrEnum):
    MODEL = "model"
    HARNESS = "harness"
    ARTIFACT = "artifact"


class BenchmarkSplit(StrEnum):
    TRAIN = "train"
    VALIDATE = "validate"


class RunState(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class ContentRef:
    """Stable reference to a file or directory owned by ContentStore."""

    uri: str
    digest: str
    media_type: str | None = None


@dataclass(slots=True)
class ModelVersion:
    version_id: str
    name: str
    content: ContentRef
    created_at: str
    parent_version_id: str | None = None


@dataclass(slots=True)
class HarnessVersion:
    """One snapshot of the complete user Harness directory."""

    version_id: str
    name: str
    content: ContentRef
    created_at: str
    parent_version_id: str | None = None


@dataclass(slots=True)
class ArtifactVersion:
    """One version of the user product, such as a game or generated application."""

    version_id: str
    name: str
    content: ContentRef
    created_at: str
    parent_version_id: str | None = None


@dataclass(slots=True)
class SolutionVersion:
    """Model, Harness, and user artifacts evaluated together."""

    solution_id: str
    model: ModelVersion
    harness: HarnessVersion
    created_at: str
    artifacts: tuple[ArtifactVersion, ...] = ()
    parent_solution_id: str | None = None


@dataclass(slots=True)
class HarnessChange:
    """A single patch proposed against the current Harness version."""

    patch: ContentRef
    reason: str


@dataclass(slots=True)
class ModelChange:
    """A single training request proposed against the current Model version."""

    training_data: ContentRef
    config: JsonObject
    reason: str


@dataclass(slots=True)
class ArtifactChange:
    """A single patch that creates or updates one user artifact."""

    artifact_name: str
    patch: ContentRef
    reason: str


ResearchChange: TypeAlias = HarnessChange | ModelChange | ArtifactChange


@dataclass(slots=True)
class Task:
    """Public benchmark input. Hidden expected data stays inside Verifier."""

    task_id: str
    input: JsonValue | ContentRef
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class TrajectoryRef:
    trajectory_id: str


@dataclass(slots=True)
class TaskResult:
    """Primary Task output plus the trace that produced it."""

    output: ContentRef
    trajectory: TrajectoryRef


@dataclass(slots=True)
class Trajectory:
    """Index of the complete trace produced by one Agent or Harness call."""

    trajectory_id: str
    agent_id: str
    content: ContentRef
    usage: JsonObject
    created_at: str
    task_id: str | None = None
    solution_id: str | None = None


@dataclass(slots=True)
class VerificationResult:
    """Verifier result for one Task."""

    task_id: str
    trajectory_id: str
    passed: bool
    score: float
    metrics: dict[str, float]
    feedback: str
    attachments: tuple[ContentRef, ...] = ()


@dataclass(slots=True)
class EvaluationReport:
    """Compact aggregate; safe to pass to selection without case details."""

    solution_id: str
    split: BenchmarkSplit
    passed: bool
    score: float
    metrics: dict[str, float]
    created_at: str


@dataclass(slots=True)
class MemoryRecord:
    memory_id: str
    text: str
    source: str
    tags: tuple[str, ...]
    created_at: str


@dataclass(slots=True)
class MemoryHit:
    memory: MemoryRecord
    score: float


@dataclass(slots=True)
class AgentSpec:
    """Explicit dependencies used to assemble one infrastructure Agent."""

    agent_id: str
    # Runtime registry key, for example "deepseek-harness@v1".
    runtime: str
    model: ModelVersion
    prompt_templates: tuple[ContentRef, ...] = ()
    skills: tuple[ContentRef, ...] = ()
    # Registry keys in "name@version" form.
    tools: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()


@dataclass(slots=True)
class ResearchIteration:
    """Persisted state of one step in an independent research line."""

    iteration: int
    input_solution_id: str
    train_report: EvaluationReport
    trajectories: ContentRef
    change: ResearchChange
    candidate_solution_id: str
    validation_report: EvaluationReport
    # None means this completed iteration ended the research line.
    next_solution_id: str | None
    started_at: str
    finished_at: str


@dataclass(slots=True)
class ResearchAgentState:
    agent_id: str
    state: RunState
    iterations: tuple[ResearchIteration, ...] = ()
    error: str | None = None


@dataclass(slots=True)
class ExperimentPlan:
    goal: str
    workflow: str
    benchmark: str
    baseline_solution_id: str
    target: EvolutionTarget
    max_iterations: int
    research_agent_count: int = 1
    stop_when_goal_reached: bool = True
    config: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class ExperimentRun:
    run_id: str
    plan: ExperimentPlan
    state: RunState
    best_solution_id: str
    research_agents: tuple[ResearchAgentState, ...] = ()
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
