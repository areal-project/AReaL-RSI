"""Frozen evaluation configuration, artifact identities, and result records."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class FinalEvalConfig:
    benchmark_repository: str
    historical_environment: str
    data_root: str
    model: str
    base_model: str
    embedding_model: str
    judge_model: str
    checkpoint_manifest: str
    checkpoint_manifest_sha256: str
    asset_lock: str
    asset_lock_sha256: str
    data_python: str
    vllm_python: str
    qwen_overlay: str
    output: str
    seed: int = 42


@dataclass(frozen=True, slots=True)
class TokenCounters:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class CheckpointExportReceipt:
    source_manifest: str
    source_sha256: str
    export_manifest: str
    export_sha256: str
    unchanged_weight_files: tuple[str, ...]
    added_legacy_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvaluationArtifact:
    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class ContextBudgetExhaustion:
    query_id: str
    input_tokens: int
    requested_output_tokens: int
    context_tokens: int
    original_record: EvaluationArtifact
    raw_messages: EvaluationArtifact


@dataclass(frozen=True, slots=True)
class EvaluationRun:
    query_id: str
    search_calls: int
    elapsed_seconds: float
    source: EvaluationArtifact
    context_budget_exhaustion: ContextBudgetExhaustion | None = None


@dataclass(frozen=True, slots=True)
class ToolArgumentFailure:
    query_id: str
    result_index: int
    kind: str
    arguments_sha256: str
    observation_sha256: str
    original_record: EvaluationArtifact
    raw_messages: EvaluationArtifact


@dataclass(frozen=True, slots=True)
class SolverVerification:
    source_directory: str
    questions: int
    completed: int
    incomplete: int
    context_budget_exhaustions: tuple[ContextBudgetExhaustion, ...]
    tool_argument_failures: tuple[ToolArgumentFailure, ...]
    artifacts: tuple[EvaluationArtifact, ...]


@dataclass(frozen=True, slots=True)
class FinalEvaluationSummary:
    questions: int
    correct: int
    accuracy: float
    official_metrics: dict[str, Any]
    mean_search_calls: float
    mean_query_seconds: float
    elapsed_seconds: float
    official_results: str
    status: str
    context_budget_exhaustions: int = 0
    recovery_receipt: str | None = None


@dataclass(frozen=True, slots=True)
class SummaryRecoveryReceipt:
    policy: str
    source_directory: str
    source_job_id: str
    source_job_state: str
    source_job_elapsed_seconds: int
    recovery_source_sha256: str
    inputs: tuple[EvaluationArtifact, ...]
    context_budget_exhaustions: tuple[ContextBudgetExhaustion, ...]
    scope: str


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(4 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def artifact(path: Path) -> EvaluationArtifact:
    return EvaluationArtifact(str(path.resolve()), digest(path))
