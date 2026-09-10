"""Records for open-ended research; iterations deliberately have no task taxonomy."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

from areal_rsi.api.base import ContentRef, JsonObject, RecordRef
from areal_rsi.api.base import JsonValue as JsonValue  # Recursive JsonObject type resolution.


@dataclass(slots=True)
class ResearchLimits:
    max_agents: int = 1
    max_nodes: int | None = 20
    max_model_calls: int | None = 200
    max_total_tokens: int | None = 1_000_000
    max_wall_seconds: float | None = 14400
    max_verify_calls: int | None = 20
    max_jobs: int | None = 1
    gpu_ids: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ("*",)

    def __post_init__(self) -> None:
        for name in (
            "max_agents",
            "max_nodes",
            "max_model_calls",
            "max_total_tokens",
            "max_wall_seconds",
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
            if name != "max_wall_seconds" and not isinstance(value, int):
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

    def __post_init__(self) -> None:
        if not self.objective.strip():
            raise ValueError("Research objective must not be empty")


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

    @staticmethod
    def message_from(value: dict[str, Any]) -> str:
        """Read old seven-field reports without rewriting their archives."""
        if isinstance(value.get("message"), str):
            return str(value["message"])
        labels = {
            "task_status": "任务",
            "work": "工作",
            "problems": "问题",
            "results": "结果",
            "interpretation": "结论",
            "next_steps": "下一步",
        }
        parts = [f"{label}：{value[name]}" for name, label in labels.items() if value.get(name)]
        parts.extend(str(item) for item in value.get("shared_findings", []))
        return "\n\n".join(parts)

    @classmethod
    def from_record(cls, value: dict[str, Any]) -> ResearchReport:
        return cls(
            value["report_id"],
            value["iteration_id"],
            value["assignment_id"],
            cls.message_from(value),
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
