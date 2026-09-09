"""Small, serializable base objects used by the public RSI API."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeAlias

JsonPrimitive: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]

TRAJECTORY_BUNDLE_MEDIA_TYPE = "application/vnd.areal-rsi.trajectories+json"
EVIDENCE_BUNDLE_MEDIA_TYPE = "application/vnd.areal-rsi.evidence+json"


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


class TrajectoryStorageType(StrEnum):
    """Where a TrajectoryStore keeps its canonical trajectory records."""

    LOCAL = "local"
    REMOTE = "remote"


class TrajectoryMaterialization(StrEnum):
    """How a store handles trajectory content owned by another system."""

    REFERENCE = "reference"
    SNAPSHOT = "snapshot"


class VerificationStatus(StrEnum):
    """Execution status of a Verifier, separate from task correctness."""

    SUCCEEDED = "succeeded"
    INVALID_INPUT = "invalid_input"
    TIMEOUT = "timeout"
    INFRA_ERROR = "infra_error"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class ContentRef:
    """引用 ContentStore 中的内容：uri 用于定位，digest 用于校验。

    media_type 描述内容格式，不描述它在某次实验中的用途。此对象不包含
    导出文件名、所属任务或访问权限；同一份内容可以被不同场景复用。
    """

    uri: str
    digest: str
    media_type: str | None = None


@dataclass(slots=True)
class EvidenceFile:
    """描述一份内容在本次证据交接中的身份，不另存一份文件内容。

    ContentRef 只回答“内容在哪里、如何校验”；此对象补充“导出叫什么、
    用来做什么、属于哪个任务”。同一 ContentRef 可以出现在不同证据清单中，
    使用不同路径或用途。路径、用途和元数据由生产者定义，不绑定 Benchmark。
    """

    # 导出到 Agent 工作目录时的相对文件路径，不是底层存储地址。
    path: str
    # 引用已有内容；文件字节仍由 ContentStore 保存。
    content: ContentRef
    # 例如 output、trajectory、verification；供访问策略筛选，本身不授予权限。
    kind: str
    # 例如 task_id、trajectory_id；被授权文件的这些信息也会出现在导出清单中。
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class EvidenceBundle:
    """一组证据文件的清单，只关联内容引用，不拼接各文件的正文。

    metadata 描述整组证据，例如所属 solution_id 和 split；具体研究方法
    负责解释这些字段。清单不等于授权，导出时还需显式选择允许访问的路径。
    """

    files: tuple[EvidenceFile, ...]
    metadata: JsonObject = field(default_factory=dict)


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
    # None selects the TrajectoryStore already injected into the caller.
    store_id: str | None = None


@dataclass(slots=True)
class TrajectorySourceSpec:
    """Configuration for a Runtime-specific source of raw trajectories."""

    provider: str
    config: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class TrajectoryFetchRequest:
    """Provider-neutral request for raw local or remote trajectory payloads."""

    source: TrajectorySourceSpec
    task_ids: tuple[str, ...] = ()
    include_content: bool = True


@dataclass(slots=True)
class TrajectoryStoreSpec:
    """Select and configure a TrajectoryStore implementation.

    ``provider`` is a factory registry key, such as ``filesystem`` or a remote
    platform adapter name. ``uri`` is the implementation's storage root and
    must not contain credentials. Remote credentials belong in environment
    variables named by ``config``.
    """

    store_id: str
    provider: str
    storage_type: TrajectoryStorageType
    uri: str
    materialization: TrajectoryMaterialization = TrajectoryMaterialization.SNAPSHOT
    config: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.storage_type = TrajectoryStorageType(self.storage_type)
        self.materialization = TrajectoryMaterialization(self.materialization)
        if not self.store_id.strip():
            raise ValueError("TrajectoryStoreSpec.store_id must not be empty.")
        if not self.provider.strip():
            raise ValueError("TrajectoryStoreSpec.provider must not be empty.")
        if not self.uri.strip():
            raise ValueError("TrajectoryStoreSpec.uri must not be empty.")


@dataclass(slots=True)
class TrajectoryQuery:
    """Portable filters understood by every TrajectoryStore implementation."""

    task_id: str | None = None
    solution_id: str | None = None
    agent_id: str | None = None
    limit: int = 100
    cursor: str | None = None

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError("TrajectoryQuery.limit must be greater than zero.")


@dataclass(slots=True)
class TrajectoryPage:
    trajectories: tuple[TrajectoryRef, ...]
    next_cursor: str | None = None


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
    source: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class VerifierSpec:
    """Select and configure a user-provided Verifier implementation."""

    verifier_id: str
    provider: str
    entrypoint: str | None = None
    digest: str | None = None
    config: JsonObject = field(default_factory=dict)
    timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        if not self.verifier_id.strip():
            raise ValueError("VerifierSpec.verifier_id must not be empty.")
        if not self.provider.strip():
            raise ValueError("VerifierSpec.provider must not be empty.")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("VerifierSpec.timeout_seconds must be greater than zero.")


@dataclass(slots=True)
class VerificationRequest:
    """Complete immutable-by-convention input for one Verifier invocation."""

    task: Task
    solution: SolutionVersion
    result: TaskResult
    verifier: VerifierSpec
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class VerificationError:
    """Structured failure information safe to persist in evaluation artifacts."""

    code: str
    message: str
    details: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class VerificationResult:
    """Verifier result for one Task."""

    task_id: str
    trajectory_id: str
    passed: bool | None
    score: float | None
    metrics: dict[str, float]
    feedback: str
    attachments: tuple[ContentRef, ...] = ()
    status: VerificationStatus = VerificationStatus.SUCCEEDED
    verifier_id: str | None = None
    verifier_digest: str | None = None
    error: VerificationError | None = None

    def __post_init__(self) -> None:
        self.status = VerificationStatus(self.status)
        if self.status is VerificationStatus.SUCCEEDED:
            if self.passed is None or self.score is None:
                raise ValueError("Successful VerificationResult requires passed and score.")
            if self.error is not None:
                raise ValueError("Successful VerificationResult cannot contain error.")
        elif self.passed is not None or self.score is not None:
            raise ValueError("Unsuccessful Verifier execution must not report passed or score.")

        if self.score is not None and not math.isfinite(self.score):
            raise ValueError("VerificationResult.score must be finite.")
        if any(not math.isfinite(value) for value in self.metrics.values()):
            raise ValueError("VerificationResult.metrics values must be finite.")


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
    # Runtime registry key, for example "areal-agent-runtime@v1".
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
    evidence: ContentRef
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
