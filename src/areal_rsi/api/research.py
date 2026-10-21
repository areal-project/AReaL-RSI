"""Records for open-ended research; iterations deliberately have no task taxonomy."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from areal_rsi.api.execution import AReaLRuntime, JobSpec, TrainingResult
from areal_rsi.api.records import (
    BenchmarkSplit,
    ContentRef,
    EvaluationResult,
    JsonObject,
    PairedComparison,
    RecordRef,
    SolutionVersion,
    SourceSnapshot,
)
from areal_rsi.api.records import JsonValue as JsonValue  # Recursive JsonObject type resolution.


class SessionPhase(StrEnum):
    CHAT = "chat"
    INTERACTION = "interaction"
    LEADER = "leader"


class SessionStatus(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting"
    PAUSED = "paused"
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"
    BUDGET_LIMITED = "budget_limited"


ReasoningEffort = Literal["low", "medium", "high", "xhigh"]
DEFAULT_DATA_ROOT = "~/.local/share/areal-rsi"


@dataclass(slots=True)
class SessionConfig:
    workspace: str
    model: str = "gpt-6.1-sol"
    effort: ReasoningEffort = "xhigh"
    base_url: str = "https://htnn-gateway.alipay.com/arenaonlinegateway/api"
    api_key_env: str = "RSI_API_KEY"
    codex_bin: str = "codex"
    env_files: list[str] = field(default_factory=list)  # Paths only; never persist credentials.
    sandbox: Literal["read-only", "workspace-write", "danger-full-access"] = "danger-full-access"
    approval_policy: Literal["on-request", "never"] = "never"
    keep_awake: bool = True
    use_experience: bool = True
    auto_learn: bool = True  # False waits for an explicit /learn after goal completion.


@dataclass(slots=True)
class AppConfig:
    """Startup defaults; each conversation persists its own SessionConfig."""

    session: SessionConfig
    data_root: str = DEFAULT_DATA_ROOT


@dataclass(slots=True)
class ExperienceRef:
    slug: str
    title: str
    description: str
    path: str


@dataclass(slots=True)
class LearningRecord:
    status: Literal["idle", "awaiting_feedback", "running", "completed", "failed"] = "idle"
    source_turn: str = ""
    document: ExperienceRef | None = None
    error: str = ""


@dataclass(slots=True)
class SessionInput:
    message_id: str
    text: str
    images: list[str] = field(default_factory=list)
    automatic: bool = False


@dataclass(slots=True)
class GoalProgress:
    summary: str = ""
    next_step: str = ""
    evidence: list[str] = field(default_factory=list)
    decision: Literal["active", "completed", "blocked"] = "active"
    question: str = ""


@dataclass(slots=True)
class ToolReceipt:
    tool: str
    arguments_digest: str
    text: str
    success: bool


@dataclass(slots=True)
class SessionRecord:
    """The single authoritative lifecycle record; Codex owns conversation history."""

    run_id: str
    config: SessionConfig
    phase: SessionPhase = SessionPhase.CHAT
    status: SessionStatus = SessionStatus.IDLE
    thread_id: str = ""
    source_thread_id: str = ""
    active_turn_id: str = ""
    last_turn_id: str = ""
    goal: TaskBrief | None = None
    progress: GoalProgress = field(default_factory=GoalProgress)
    next_phase: SessionPhase | None = None
    handoff_pending: bool = False
    pending_inputs: list[SessionInput] = field(default_factory=list)
    in_flight: SessionInput | None = None
    tokens_used: int = 0
    seconds_used: float = 0
    # Native counters are cumulative per thread; a fork may inherit its parent's usage.
    thread_tokens: dict[str, int] = field(default_factory=dict)
    empty_continuations: int = 0
    last_error: str = ""
    receipts: dict[str, ToolReceipt] = field(default_factory=dict)
    experience: list[ExperienceRef] = field(default_factory=list)
    learning: LearningRecord = field(default_factory=LearningRecord)
    pending_prompts: list[UserPrompt] = field(default_factory=list)
    problem: ProblemBinding | None = None


@dataclass(slots=True)
class PromptQuestion:
    question_id: str
    text: str
    options: list[str] = field(default_factory=list)


@dataclass(slots=True)
class UserPrompt:
    request_id: int | str
    kind: Literal["approval", "question"]
    text: str
    questions: list[PromptQuestion] = field(default_factory=list)
    prompt_id: str = ""  # Public identity; native request IDs can repeat after reconnect.


@dataclass(slots=True)
class SessionEvent:
    kind: Literal["message", "delta", "tool", "status", "prompt", "error"]
    role: str = "system"
    text: str = ""
    item_id: str = ""
    prompt: UserPrompt | None = None
    completed: bool = False


@dataclass(slots=True)
class ResearchLimits:
    max_agents: int = (
        1  # Simultaneously live researchers, including waiting; not lifetime creations.
    )
    max_nodes: int | None = None
    # Retained for configuration compatibility; execution rejects non-null caps.
    max_model_calls: int | None = None
    max_total_tokens: int | None = None
    max_wall_seconds: float | None = None
    max_verify_calls: int | None = None
    max_jobs: int | None = 1
    gpu_ids: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ("*",)
    max_effective_seconds: float | None = None  # Per researcher; wall limit stays independent.

    def __post_init__(self) -> None:
        for name in (
            "max_agents",
            "max_nodes",
            "max_model_calls",
            "max_total_tokens",
            "max_wall_seconds",
            "max_effective_seconds",
            "max_verify_calls",
            "max_jobs",
        ):
            value = getattr(self, name)
            if value is None and name != "max_agents":
                continue  # Explicit null means no run-level cap.
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ValueError(f"{name} must be a valid nonnegative number")
            if value == 0 and name != "max_verify_calls":
                raise ValueError(f"{name} must be positive")
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
            if name not in {"max_wall_seconds", "max_effective_seconds"} and not isinstance(
                value, int
            ):
                raise ValueError(f"{name} must be an integer")
        if len(set(self.gpu_ids)) != len(self.gpu_ids):
            raise ValueError("GPU identifiers must be unique")
        if not self.allowed_paths or any(
            not p or p.startswith("/") or ".." in p.split("/") or "\\" in p
            for p in self.allowed_paths
        ):
            raise ValueError("allowed_paths must contain relative file patterns")


@dataclass(slots=True)
class VerificationContract:
    """Frozen comparison conditions; executable details are operator-owned config."""

    metric: str
    direction: Literal["maximize", "minimize"] = "maximize"
    target: float | None = None
    description: str = ""
    identity: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.metric.strip() or self.direction not in {"maximize", "minimize"}:
            raise ValueError("A verification metric and comparison direction are required")
        if self.target is not None and not math.isfinite(self.target):
            raise ValueError("Verification target must be finite")


@dataclass(slots=True)
class TaskBrief:
    """Both structured limits and the user's nuanced instructions are canonical."""

    objective: str
    instructions: str
    limits: ResearchLimits = field(default_factory=ResearchLimits)
    verification: VerificationContract | None = None
    revision: int = 0
    acceptance: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.objective.strip():
            raise ValueError("Research objective must not be empty")
        if self.revision < 0:
            raise ValueError("Goal revision must be nonnegative")
        if any(not isinstance(item, str) or not item.strip() for item in self.acceptance):
            raise ValueError("验收标准须为非空文字列表")
        if self.verification is not None and not isinstance(
            self.verification, VerificationContract
        ):
            raise ValueError(
                "verification 只能包含 metric、direction、target、description、identity；"
                "具体评测约定请放在 description 或 instructions 中"
            )


@dataclass(frozen=True, slots=True)
class ProblemBinding:
    """Operator-selected adapter; config and adapter bytes are pinned on creation."""

    entrypoint: str
    config: ContentRef
    adapter: ContentRef | None = None
    objective: str = ""


@dataclass(frozen=True, slots=True)
class SourceTree:
    root: str
    paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ComparisonContract:
    """Fixed comparison conditions; evolving solution contents live separately."""

    metric: str = "score"
    direction: Literal["maximize", "minimize"] = "maximize"
    min_rounds: int = 1
    min_improvement: float = 0.0
    target_delta: float | None = None
    conditions: tuple[ContentRef, ...] = ()

    def __post_init__(self) -> None:
        if not self.metric or self.direction not in {"maximize", "minimize"}:
            raise ValueError("A comparison requires a metric and direction")
        if self.min_rounds < 0 or not math.isfinite(self.min_improvement):
            raise ValueError("Invalid comparison requirements")
        if self.target_delta is not None and not math.isfinite(self.target_delta):
            raise ValueError("Invalid comparison target")


@dataclass(frozen=True, slots=True)
class ProblemSpec:
    name: str
    instructions: str
    baseline: SolutionVersion
    model_manifest: ContentRef
    contract: ComparisonContract = field(default_factory=ComparisonContract)
    locked_files: tuple[ContentRef, ...] = ()
    sources: tuple[SourceTree, ...] = ()


@dataclass(frozen=True, slots=True)
class CandidateContext:
    run_id: str
    root: str
    candidate_id: str
    parent: SolutionVersion
    problem: ProblemSpec
    output: str


@dataclass(frozen=True, slots=True)
class CandidatePlan:
    """A frozen solution, optionally produced by a training job."""

    solution: SolutionVersion
    inputs: tuple[ContentRef, ...] = ()
    job: JobSpec | None = None
    runtime: AReaLRuntime | None = None

    def __post_init__(self) -> None:
        if (self.job is None) != (self.runtime is None):
            raise ValueError("Training requires both a job and an AReaL runtime")


@dataclass(frozen=True, slots=True)
class EvaluationContext:
    run_id: str
    root: str
    problem: ProblemSpec
    solution: SolutionVersion
    split: BenchmarkSplit
    output: str


@dataclass(frozen=True, slots=True)
class EvaluationPlan:
    job: JobSpec
    task_ids: tuple[str, ...]
    inputs: tuple[ContentRef, ...] = ()
    # Repeated samples belong to the same independent question.
    clusters: dict[str, str] | None = None
    sources: tuple[SourceSnapshot, ...] = ()


class CandidateStatus(StrEnum):
    PREPARED = "prepared"
    PREPARING = "preparing"
    TRAINING = "training"
    EVALUATING = "evaluating"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


@dataclass(slots=True)
class CandidateRecord:
    candidate_id: str
    parent_id: str | None
    hypothesis: str
    recipe: ContentRef | None
    plan: CandidatePlan | None
    sources: tuple[SourceSnapshot, ...] = ()
    status: CandidateStatus = CandidateStatus.PREPARED
    solution: SolutionVersion | None = None
    training: TrainingResult | None = None
    validation: EvaluationResult | None = None
    test: EvaluationResult | None = None
    validation_attempt: int = 1
    test_attempt: int = 1
    validation_plan: ContentRef | None = None
    test_plan: ContentRef | None = None
    error: str = ""


@dataclass(frozen=True, slots=True)
class CandidateSelection:
    candidate_id: str
    solution: SolutionVersion
    protocol: ContentRef
    validation: ContentRef
    selected_at: str


@dataclass(frozen=True, slots=True)
class FinalEvaluation:
    selection: ContentRef
    baseline: EvaluationResult
    candidate: EvaluationResult
    improvement: float
    target_met: bool
    comparison: PairedComparison | None = None


@dataclass(frozen=True, slots=True)
class CandidateFeedback:
    candidate_id: str
    parent_id: str | None
    hypothesis: str
    status: CandidateStatus
    score: float | None
    metrics: dict[str, float]
    training_steps: int | None
    evidence: tuple[ContentRef, ...]
    error: str


@dataclass(slots=True)
class Assignment:
    assignment_id: str
    agent_id: str
    instruction: str
    completion_criteria: str
    created_at: str
    references: tuple[RecordRef, ...] = ()
    reply_to_report_id: str | None = None

    def __post_init__(self) -> None:
        if not self.instruction.strip():
            raise ValueError("Assignment requires a concrete instruction")


@dataclass(slots=True)
class ResearchReport:
    """A researcher's reply, with system-owned identity and evidence references."""

    report_id: str
    iteration_id: str
    assignment_id: str
    message: str
    created_at: str
    references: tuple[RecordRef, ...] = ()

    def __post_init__(self) -> None:
        if not self.message.strip():
            raise ValueError("Report requires a nonempty message")

    @classmethod
    def from_record(cls, value: dict[str, Any]) -> ResearchReport:
        return cls(
            value["report_id"],
            value["iteration_id"],
            value["assignment_id"],
            value["message"],
            value["created_at"],
            tuple(RecordRef(item["uri"]) for item in value.get("references", [])),
        )


@dataclass(slots=True)
class EvidenceRecord:
    evidence_id: str
    agent_id: str
    title: str
    summary: str
    created_at: str
    iteration_id: str | None = None
    references: tuple[RecordRef, ...] = ()
    shared: bool = False
    limitations: str = ""
    supersedes: str | None = None


@dataclass(slots=True)
class CandidateSnapshot:
    candidate_id: str
    content: ContentRef
    created_at: str
    source_agent_id: str
    description: str
    parent_candidate_id: str | None = None


@dataclass(slots=True)
class VerifiedResult:
    """Produced by a trusted verifier adapter, never by parsing an agent's report."""

    result_id: str
    candidate_id: str
    contract_digest: str
    created_at: str
    status: Literal["succeeded", "failed", "infra_error", "cancelled"]
    score: float | None = None
    passed: bool | None = None
    metrics: dict[str, float] = field(default_factory=dict)
    phase: Literal["search", "final", "diagnostic"] = "search"
    simulated: bool = False
    references: tuple[RecordRef, ...] = ()
    error: str | None = None
    goal_revision: int = 0

    def __post_init__(self) -> None:
        if self.status not in {"succeeded", "failed", "infra_error", "cancelled"}:
            raise ValueError("Unknown verification status")
        if self.phase not in {"search", "final", "diagnostic"}:
            raise ValueError("Unknown verification phase")
        if self.status == "succeeded":
            if self.score is None or self.passed is None or self.error is not None:
                raise ValueError("Successful verification requires score and passed, without error")
        elif self.score is not None or self.passed is not None:
            raise ValueError("Unsuccessful verification must not contain a score or passed flag")
        if any(not math.isfinite(v) for v in self.metrics.values()) or (
            self.score is not None and not math.isfinite(self.score)
        ):
            raise ValueError("Verification metrics must be finite")


@dataclass(slots=True)
class HarnessIteration:
    """A measured harness snapshot and the evidence motivating its change."""

    candidate: CandidateSnapshot
    verification: VerifiedResult
    evidence: EvidenceRecord | None = None


@dataclass(slots=True)
class JobWaiter:
    iteration_id: str | None = None
    notified: bool = False


@dataclass(slots=True)
class ResearchJob:
    """Research ownership and publication state; process state belongs to JobRecord."""

    agent_id: str
    output: str
    spec: JobSpec
    created_at: str
    iteration_id: str | None = None
    candidate_id: str | None = None
    source_job_id: str | None = None
    verification: bool = False
    phase: Literal["search", "final", "diagnostic", "prepare"] = "diagnostic"
    parameters: dict[str, str] | None = None
    waiting: bool = False
    processed: bool = False
    automatic: bool = False
    completion: bool = False
    output_subdir: str = "."
    public_output_subdir: str | None = None
    feedback_file: str | None = "feedback.json"
    queue_timing_file: str | None = None
    auto_verify_output: str | None = None
    waiters: dict[str, JobWaiter] = field(default_factory=dict)
    result: VerifiedResult | None = None
    result_ref: RecordRef | None = None
    # Public receipts and arbitrary tool feedback are transport payloads, not control state.
    execution_result: JsonObject | None = None
    feedback: JsonObject | None = None
    published_candidate_id: str | None = None
    verification_job_id: str | None = None
    publication_error: str | None = None
    goal_revision: int = 0


@dataclass(slots=True)
class CompletionRecord:
    """Optional result confirmation, independent of whether a numeric target was reached."""

    status: Literal["running", "interrupted", "failed", "completed"]
    exploration_finished_at: float | None = None
    target_reached: bool | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"running", "interrupted", "failed", "completed"}:
            raise ValueError("Unknown completion status")
        if self.exploration_finished_at is not None and not math.isfinite(
            self.exploration_finished_at
        ):
            raise ValueError("Completion cutoff must be finite")


@dataclass(slots=True)
class ResearchMessage:
    message_id: str
    sender: str
    recipient: str
    text: str
    created_at: str
    references: tuple[RecordRef, ...] = ()
    reply_to: str | None = None


@dataclass(slots=True)
class ResearchEvent:
    sequence: int
    kind: str
    created_at: str
    agent_id: str | None = None
    iteration_id: str | None = None
    data: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class IntakeState:
    status: str | None = None  # None means no conversational intake was requested.
    summary: str = ""
    environment: str = ""
    config_revision: str = ""
    prepared_at: str | None = None
    execution_required: bool = False
    confirmation_message_id: str | None = None


@dataclass(slots=True)
class TurnRetry:
    message_id: str
    retry_id: str


@dataclass(slots=True)
class RetryBackoff:
    count: int = 0
    at: float | None = None


@dataclass(slots=True)
class RunningTool:
    name: str
    started_at: str


@dataclass(slots=True)
class AgentActivity:
    type: str
    tool: str | None
    updated_at: str
    started_at: str | None = None
    last_progress_at: str | None = None
    api_available: bool | None = None
    active_tools: dict[str, RunningTool] = field(default_factory=dict)


@dataclass(slots=True)
class AgentTraceEntry:
    sequence: int
    created_at: str
    kind: str
    text: str = ""
    name: str = ""
    call_id: str = ""
    arguments: str = ""
    result: str = ""
    status: str = ""
    started_at: str | None = None
    job_id: str = ""


@dataclass(slots=True)
class AgentTracePage:
    agent_id: str
    entries: list[AgentTraceEntry]
    next_before: int | None = None


@dataclass(slots=True)
class RuntimeUsage:
    agent_id: str
    usage: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class ReportSummary:
    uri: str
    message: str
    created_at: str


@dataclass(slots=True)
class UserQuestion:
    message_id: str
    agent_id: str = ""
    question: str = ""
    created_at: str = ""


@dataclass(slots=True)
class PublishedResult(VerifiedResult):
    uri: str = ""


@dataclass(slots=True)
class TargetObservation:
    result: PublishedResult
    observed_at: str


@dataclass(slots=True)
class AgentClock:
    started_at: float
    finished_at: float | None = None
    api_spans: list[tuple[float, float | None]] = field(default_factory=list)
    queue_spans: dict[str, tuple[float, float | None]] = field(default_factory=dict)


@dataclass(slots=True)
class EffectiveClock:
    protocol: str = "baseline-active-v1"
    periods: list[tuple[float, float | None]] = field(default_factory=list)
    agents: dict[str, AgentClock] = field(default_factory=dict)
    heartbeat_at: float = 0.0


@dataclass(slots=True)
class GoalContext:
    """Public working context, distinct from a model's private reasoning/history."""

    status: Literal["active", "blocked", "completed"] = "active"
    summary: str = ""
    current_step: str = ""
    next_steps: list[str] = field(default_factory=list)
    acceptance: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    blocker: str = ""
    candidate_id: str = ""
    updated_at: str = ""
    revision: int = 0


@dataclass(slots=True)
class RunAcceptance:
    """Immutable Leader decision, recoverable independently of mutable run views."""

    goal: GoalContext
    brief: TaskBrief
    final_result_ref: RecordRef | None = None
    user_messages: tuple[ResearchMessage, ...] = ()


@dataclass(slots=True)
class TeamTask:
    """Future delegation contract. A task is a portion of one run's goal."""

    task_id: str
    run_id: str
    instruction: str
    acceptance: tuple[str, ...]
    input_refs: tuple[RecordRef, ...] = ()
    researcher_id: str | None = None
    status: Literal["pending", "running", "completed", "blocked", "cancelled"] = "pending"


@dataclass(slots=True)
class TaskReport:
    """Future Researcher output; completing a task never accepts the run."""

    task_id: str
    researcher_id: str
    summary: str
    evidence: tuple[RecordRef, ...]
    candidate_id: str | None = None
    blocker: str | None = None


@dataclass(slots=True)
class CoordinationState:
    """Durable scheduling state. JSON is decoded once when a run is opened."""

    status: str = "planned"
    created_at: str = ""
    simulated: bool = False
    inboxes: dict[str, list[str]] = field(default_factory=dict)
    active: dict[str, str] = field(default_factory=dict)
    delivered: list[str] = field(default_factory=list)
    plan: str = ""
    workspace_dir: str | None = None
    workspace_base_commit: str | None = None
    seed_candidate_id: str | None = None
    baseline_candidate_id: str | None = None
    selected_candidate_id: str | None = None
    model_calls: int = 0
    total_tokens: int = 0
    verify_calls: int = 0
    started_at: str | None = None
    waiting_for_user: list[UserQuestion] = field(default_factory=list)
    finalization: CompletionRecord | None = None
    goal: GoalContext | None = None
    intake: IntakeState = field(default_factory=IntakeState)
    effective_time: EffectiveClock | None = None
    turn_retries: dict[str, TurnRetry] = field(default_factory=dict)
    turn_followups: dict[str, list[str]] = field(default_factory=dict)
    runtime_usage: dict[str, RuntimeUsage] = field(default_factory=dict)
    agent_activity: dict[str, AgentActivity] = field(default_factory=dict)
    passk_reports: dict[str, ReportSummary] = field(default_factory=dict)
    passk_retries: dict[str, RetryBackoff] = field(default_factory=dict)
    passk_target: TargetObservation | None = None
    passk_ready: bool = False
    passk_supervision_at: float = 0.0
    runtime_turns: int = 0
    mode: str | None = None
    budget_stop_reason: str | None = None
    user_stopped: bool = False
    require_final_confirmation: bool = False

    def __post_init__(self) -> None:
        if self.status not in {
            "planned",
            "running",
            "paused",
            "stopped",
            "succeeded",
            "completed",
            "failed",
            "cancelled",
        }:
            raise ValueError("Unknown coordination status")


@dataclass(slots=True)
class SessionSummary:
    run_id: str
    workspace: str
    phase: SessionPhase
    status: SessionStatus
    objective: str
    progress: GoalProgress
    error: str
    learning: LearningRecord
    prompts: list[UserPrompt]


@dataclass(slots=True)
class EventPage:
    cursor: int
    events: list[SessionEvent]
    has_more: bool = False


@dataclass(slots=True)
class RequestReceipt:
    request_id: str
    status: Literal["running", "completed", "failed", "interrupted"]
    result: JsonObject = field(default_factory=dict)
    error: str = ""
