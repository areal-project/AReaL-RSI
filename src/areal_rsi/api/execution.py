"""Process jobs describe execution, never research categories or trusted scores."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from areal_rsi.api.records import ContentRef

JobState = Literal[
    "queued", "starting", "running", "succeeded", "failed", "cancelled", "timed_out", "lost"
]

TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled", "timed_out", "lost"})


@dataclass(frozen=True)
class JobSpec:
    """Only trusted application code may construct arbitrary commands.

    Agent tools should bind an ApprovedCommand instead. Local execution is not a
    security sandbox. Use a configured container command for untrusted code and
    keep verifier code/data outside candidate-writable mounts.

    ``env`` is persisted privately for queued-job recovery. Pass credentials by
    environment variable inheritance where possible; do not expose this field
    through a web API. ``mock`` exercises lifecycle/resource scheduling without
    executing the command or accessing GPUs.
    """

    command: tuple[str, ...] = ()
    cwd: str = "."
    gpu_count: int = 0
    gpu_ids: tuple[str, ...] = ()
    timeout_seconds: float | None = None
    env: dict[str, str] = field(default_factory=dict, repr=False)
    metadata: dict[str, str] = field(default_factory=dict)
    backend: Literal["local", "mock"] = "local"
    mock_duration_seconds: float = 0.0
    mock_exit_code: int = 0
    mock_stdout: str = "Simulated job; no training or evaluation was performed.\n"


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    spec: JobSpec
    state: JobState
    created_at: float
    updated_at: float
    assigned_gpu_ids: tuple[str, ...] = ()
    started_at: float | None = None
    finished_at: float | None = None
    pid: int | None = None
    process_identity: str | None = None
    launch_token: str | None = field(default=None, repr=False)
    exit_code: int | None = None
    error: str | None = None
    log_path: str = ""
    result_path: str = ""
    resources_released: bool = True
    revision: int = 0

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES


@dataclass(frozen=True)
class JobEvent:
    """Durable at-least-once notifications; consumers deduplicate event_id."""

    event_id: str
    job_id: str
    state: JobState
    timestamp: float
    record_path: str


@dataclass(frozen=True, slots=True)
class AReaLRuntime:
    repository: str
    python: str
    environment: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TrainingReceipt:
    """Written only after the trainer closes and finishes checkpoint writes."""

    checkpoint: str
    global_step: int
    metrics: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TokenizedTrainingSample:
    """Unshifted SFT targets; prompt and external observations have mask zero."""

    task_id: str
    input_ids: tuple[int, ...]
    loss_mask: tuple[int, ...]

    def __post_init__(self) -> None:
        if not self.task_id or len(self.input_ids) < 2:
            raise ValueError("SFT samples require an identity and at least two tokens")
        if len(self.input_ids) != len(self.loss_mask):
            raise ValueError("SFT tokens and loss mask have different lengths")
        if any(token < 0 for token in self.input_ids):
            raise ValueError("Token IDs must be nonnegative")
        if any(mask not in (0, 1) for mask in self.loss_mask):
            raise ValueError("SFT loss masks must be binary")
        if self.loss_mask[0] or not any(self.loss_mask):
            raise ValueError("SFT needs a conditioning prefix and trainable response tokens")


@dataclass(frozen=True, slots=True)
class TrainingResult:
    job_id: str
    checkpoint: ContentRef
    manifest: ContentRef
    global_step: int
    metrics: dict[str, float]


@dataclass(frozen=True)
class ApprovedCommand:
    """An application-owned command template, with explicit argv parameters.

    A placeholder must occupy an entire argv element, e.g. ``{candidate}``.
    Parameter substitution never invokes a shell. A shell command, container
    image, verifier entry point and mounts are configuration owned by the user,
    not agent-selected parameters. Allowed paths/values still require validation
    by the tool that owns the template.
    """

    name: str
    command: tuple[str, ...]
    parameters: tuple[str, ...] = ()
    gpu_count: int = 0
    timeout_seconds: float | None = None
    output_subdir: str = "."
    # Verifiers keep all output private unless an operator publishes a subdirectory.
    public_output_subdir: str | None = None
    # Relative to the published output directory; None disables inline feedback.
    # This is an execution receipt, never a replacement for trusted verification.
    feedback_file: str | None = "feedback.json"
    # Hidden final pools default to one candidate. Read-only result aggregation
    # and reusable public validators can explicitly accept several candidates.
    final_candidate_policy: Literal["single", "multiple"] = "single"
    # Optional operator-owned artifact directory relative to output. Successful
    # jobs publish a snapshot and run the trusted search verifier automatically.
    auto_verify_output: str | None = None
    # Optional agent-facing help for data arguments; does not change binding or validation.
    parameter_descriptions: dict[str, str] = field(default_factory=dict)
    # Trusted adapter receipt with created_at/started_at/finished_at queue timestamps.
    queue_timing_file: str | None = None

    def __post_init__(self) -> None:
        if self.final_candidate_policy not in {"single", "multiple"}:
            raise ValueError("final_candidate_policy must be single or multiple")

    def bind(self, values: dict[str, str], *, cwd: str, **metadata: str) -> JobSpec:
        if set(values) != set(self.parameters):
            raise ValueError(f"{self.name}: expected parameters {self.parameters}")
        if not self.command:
            raise ValueError("An approved command must include a fixed executable")
        placeholders = {"{" + name + "}": values[name] for name in self.parameters}
        if self.command[0] in placeholders:
            raise ValueError("The executable cannot be an agent parameter")
        return JobSpec(
            command=tuple(placeholders.get(argument, argument) for argument in self.command),
            cwd=cwd,
            gpu_count=self.gpu_count,
            timeout_seconds=self.timeout_seconds,
            metadata={**metadata, "command_template": self.name},
        )
