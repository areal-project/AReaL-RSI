"""Decisions and training lineage for the first RSI loop."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Literal

from areal_rsi.api.records import RsiEvaluationReport, valid_digest


@dataclass(frozen=True, slots=True)
class HarnessSelectionRule:
    noise_band: float
    max_relative_token_cost: float

    def __post_init__(self) -> None:
        if any(
            not math.isfinite(value) or value < 0
            for value in (self.noise_band, self.max_relative_token_cost)
        ):
            raise ValueError("Harness selection limits must be finite and nonnegative")


@dataclass(frozen=True, slots=True)
class HarnessSelectionDecision:
    selected_harness_id: str
    candidate_harness_id: str
    accepted: bool
    score_gain: float
    relative_token_cost: float
    reason: str
    rule: HarnessSelectionRule
    baseline: RsiEvaluationReport
    candidate: RsiEvaluationReport


@dataclass(frozen=True, slots=True)
class RsiRolloutEvidence:
    path: str
    digest: str
    records: int
    tasks: int

    def __post_init__(self) -> None:
        if (
            not self.path.startswith("/")
            or not valid_digest(self.digest)
            or type(self.records) is not int
            or type(self.tasks) is not int
            or not 1 <= self.tasks <= self.records
        ):
            raise ValueError("Rollout evidence needs a digest and task coverage")


@dataclass(frozen=True, slots=True)
class RsiTrainResult:
    run_id: str
    algorithm: Literal["grpo"]
    task_set_id: str
    harness_id: str
    base_model_digest: str
    checkpoint_digest: str
    runner_digest: str
    verifier_digest: str
    config_digest: str
    checkpoint_path: str
    global_step: int
    rollout: RsiRolloutEvidence

    def __post_init__(self) -> None:
        if (
            not self.run_id.strip()
            or self.algorithm != "grpo"
            or not self.task_set_id.startswith("taskset_")
            or not self.harness_id.startswith("harness_")
            or not self.checkpoint_path.startswith("/")
            or type(self.global_step) is not int
            or self.global_step < 1
            or any(
                not valid_digest(digest)
                for digest in (
                    self.base_model_digest,
                    self.checkpoint_digest,
                    self.runner_digest,
                    self.verifier_digest,
                    self.config_digest,
                )
            )
        ):
            raise ValueError("Training receipt lacks a complete checkpoint lineage")


@dataclass(frozen=True, slots=True)
class RsiTrainingSignal:
    model_digest: str
    harness_id: str
    task_set_id: str
    primary_report_digest: str
    repeat_report_digest: str
    primary_seed: int
    repeat_seed: int
    mixed_task_count: int
    total_task_count: int
    minimum_mixed_task_count: int
    accepted: bool


@dataclass(frozen=True, slots=True)
class RsiPromotionRule:
    objective: Literal["joint_performance", "model_internalization"]
    minimum_score_gain: float
    max_relative_token_cost: float
    minimum_sealed_cases: int = 100

    def __post_init__(self) -> None:
        if (
            self.objective not in {"joint_performance", "model_internalization"}
            or not math.isfinite(self.minimum_score_gain)
            or self.minimum_score_gain < 0
            or not math.isfinite(self.max_relative_token_cost)
            or self.max_relative_token_cost < 0
            or type(self.minimum_sealed_cases) is not int
            or self.minimum_sealed_cases < 1
        ):
            raise ValueError("Invalid promotion rule")


@dataclass(frozen=True, slots=True)
class RsiPromotionProposal:
    baseline_model_digest: str
    baseline_harness_id: str
    candidate_model_digest: str
    candidate_harness_id: str
    task_set_id: str
    rule: RsiPromotionRule
    dev_score_gain: float
    dev_relative_token_cost: float


@dataclass(frozen=True, slots=True)
class RsiPromotionDecision:
    proposal: RsiPromotionProposal
    accepted: bool
    sealed_score_gain: float
    sealed_relative_token_cost: float
    paired_gain_lower_bound: float
    reason: str


@dataclass(frozen=True, slots=True)
class ResearcherSpec:
    endpoint: str
    model: str
    api_key_env: str | None
    max_cases: int = 16

    def __post_init__(self) -> None:
        from urllib.parse import urlparse

        parsed = urlparse(self.endpoint)
        if (
            (
                parsed.scheme != "https"
                and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"})
            )
            or not self.model.strip()
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise ValueError("Researcher endpoint must be HTTPS or local HTTP")
        if self.api_key_env is not None and not re.fullmatch(r"[A-Z_][A-Z0-9_]*", self.api_key_env):
            raise ValueError("Researcher API key environment variable name is invalid")
        if type(self.max_cases) is not int or not 1 <= self.max_cases <= 64:
            raise ValueError("Researcher case limit must be 1..64")


@dataclass(frozen=True, slots=True)
class GenerationSpec:
    """Pre-registered paths, budgets and gates for one Slurm generation."""

    run_id: str
    task_set_root: str
    h0_root: str
    candidate_prompt: str
    runner_root: str
    model_root: str
    areal_python: str
    areal_config: str
    cluster_fileroot: str
    name_resolve_root: str
    experiment_name: str
    seed: int
    repeat_seed: int
    minimum_mixed_cases: int
    harness_rule: HarnessSelectionRule
    promotion_rule: RsiPromotionRule
    parent_release_id: str | None = None
    researcher: ResearcherSpec | None = None

    def __post_init__(self) -> None:
        from pathlib import Path

        from areal_rsi.api.control import safe_name

        safe_name(self.run_id)
        safe_name(self.experiment_name)
        for name in (
            "task_set_root",
            "h0_root",
            "candidate_prompt",
            "runner_root",
            "model_root",
            "areal_python",
            "areal_config",
            "cluster_fileroot",
            "name_resolve_root",
        ):
            if not Path(getattr(self, name)).is_absolute():
                raise ValueError(f"{name} must be absolute")
        if (
            type(self.seed) is not int
            or type(self.repeat_seed) is not int
            or self.seed < 0
            or self.repeat_seed < 0
            or self.seed == self.repeat_seed
            or type(self.minimum_mixed_cases) is not int
            or self.minimum_mixed_cases < 1
        ):
            raise ValueError("Generation seeds and signal threshold are invalid")
        if self.parent_release_id is not None and not self.parent_release_id.startswith("release_"):
            raise ValueError("Parent release ID is invalid")


@dataclass(frozen=True, slots=True)
class GenerationOutcome:
    run_id: str
    status: Literal[
        "harness_rejected", "signal_rejected", "dev_rejected", "sealed_rejected", "promoted"
    ]
    selected_harness_id: str
    trained_model_digest: str | None
    reason: str
    release_id: str | None


@dataclass(frozen=True, slots=True)
class GenerationSetup:
    spec: GenerationSpec
    task_set_id: str
    h0_id: str
    model_digest: str
    runner_digest: str
    verifier_digest: str
    config_digest: str


@dataclass(frozen=True, slots=True)
class CandidateRecord:
    prompt_digest: str
    source: Literal["supplied", "researcher"]
    feedback_digest: str


@dataclass(frozen=True, slots=True)
class ReleaseRecord:
    release_id: str
    run_id: str
    task_set_id: str
    task_set_root: str
    model_digest: str
    harness_id: str
    harness_root: str
    checkpoint_path: str
    trained_receipt_digest: str
    sealed_decision_digest: str
    parent_release_id: str | None

    def __post_init__(self) -> None:
        from pathlib import Path

        from areal_rsi.api.control import safe_name

        safe_name(self.run_id)
        if (
            not re.fullmatch(r"release_[0-9a-f]{64}|release_pending", self.release_id)
            or (
                self.parent_release_id is not None
                and not re.fullmatch(r"release_[0-9a-f]{64}", self.parent_release_id)
            )
            or not self.task_set_id.startswith("taskset_")
            or not self.harness_id.startswith("harness_")
            or any(
                not Path(value).is_absolute()
                for value in (self.task_set_root, self.harness_root, self.checkpoint_path)
            )
            or any(
                not valid_digest(value)
                for value in (
                    self.model_digest,
                    self.trained_receipt_digest,
                    self.sealed_decision_digest,
                )
            )
        ):
            raise ValueError("Release record has invalid lineage or paths")


@dataclass(frozen=True, slots=True)
class RollbackEvent:
    event_id: str
    run_id: str
    from_release_id: str
    to_release_id: str | None
    reason: str

    def __post_init__(self) -> None:
        if (
            not re.fullmatch(r"rollback_[0-9a-f]{64}|rollback_pending", self.event_id)
            or not re.fullmatch(r"release_[0-9a-f]{64}", self.from_release_id)
            or (
                self.to_release_id is not None
                and not re.fullmatch(r"release_[0-9a-f]{64}", self.to_release_id)
            )
            or not self.reason.strip()
        ):
            raise ValueError("Rollback record has invalid identity")
