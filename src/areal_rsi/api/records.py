"""Immutable identities and evidence for one TIR model/Harness generation."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")


def valid_digest(value: str) -> bool:
    return bool(SHA256.fullmatch(value))


class RsiSplit(StrEnum):
    HARNESS_EVOLVE = "harness_evolve"
    HARNESS_SELECT = "harness_select"
    MODEL_TRAIN = "model_train"
    MODEL_DEV = "model_dev"
    SEALED_TEST = "sealed_test"


@dataclass(frozen=True, slots=True)
class Task:
    task_id: str
    input: dict[str, Any]  # AReaL's JSON dataset boundary.


@dataclass(frozen=True, slots=True)
class TaskPartition:
    task_ids: tuple[str, ...]
    public_digest: str

    def __post_init__(self) -> None:
        if not self.task_ids or len(set(self.task_ids)) != len(self.task_ids):
            raise ValueError("A partition needs distinct task IDs")
        if any(not task_id.strip() for task_id in self.task_ids) or not valid_digest(
            self.public_digest
        ):
            raise ValueError("Invalid task partition identity")


@dataclass(frozen=True, slots=True)
class TaskSetVersion:
    task_set_id: str
    source_train_digest: str
    source_test_digest: str
    answers_digest: str
    harness_evolve: TaskPartition
    harness_select: TaskPartition
    model_train: TaskPartition
    model_dev: TaskPartition
    sealed_test: TaskPartition

    def __post_init__(self) -> None:
        if not self.task_set_id.startswith("taskset_") or any(
            not valid_digest(digest)
            for digest in (
                self.source_train_digest,
                self.source_test_digest,
                self.answers_digest,
            )
        ):
            raise ValueError("Invalid task set identity")
        ids = [task_id for split in RsiSplit for task_id in getattr(self, split.value).task_ids]
        if len(set(ids)) != len(ids):
            raise ValueError("Task IDs overlap across frozen splits")


@dataclass(frozen=True, slots=True)
class RsiCaseScore:
    task_id: str
    score: float
    response: str
    generated_tokens: int

    def __post_init__(self) -> None:
        if (
            not self.task_id.strip()
            or not math.isfinite(self.score)
            or not 0 <= self.score <= 1
            or type(self.generated_tokens) is not int
            or self.generated_tokens < 0
        ):
            raise ValueError("Invalid TIR case score")


@dataclass(frozen=True, slots=True)
class RsiEvaluationReport:
    model_digest: str
    harness_id: str
    task_set_id: str
    split: RsiSplit
    runner_digest: str
    verifier_digest: str
    seed: int
    temperature: float
    max_new_tokens: int
    cases: tuple[RsiCaseScore, ...]
    trajectory_path: str
    trajectory_digest: str

    def __post_init__(self) -> None:
        if any(
            not valid_digest(digest)
            for digest in (self.model_digest, self.runner_digest, self.verifier_digest)
        ) or (
            not self.harness_id.startswith("harness_")
            or not self.task_set_id.startswith("taskset_")
            or type(self.seed) is not int
            or self.seed < 0
            or not math.isfinite(self.temperature)
            or self.temperature < 0
            or type(self.max_new_tokens) is not int
            or self.max_new_tokens < 1
            or not self.cases
            or len({case.task_id for case in self.cases}) != len(self.cases)
            or not self.trajectory_path.startswith("/")
            or not valid_digest(self.trajectory_digest)
        ):
            raise ValueError("Invalid or incomplete TIR evaluation report")

    @property
    def score(self) -> float:
        return sum(case.score for case in self.cases) / len(self.cases)

    @property
    def generated_tokens(self) -> int:
        return sum(case.generated_tokens for case in self.cases)


@dataclass(frozen=True, slots=True)
class AgentHarnessSpec:
    """Frozen external solving agent and its task prompt."""

    harness_id: str
    prompt_digest: str
    executable_digest: str
    calculator_digest: str
    model: str
    timeout_seconds: int
    max_budget_usd: float

    def __post_init__(self) -> None:
        if (
            (
                not re.fullmatch(r"harness_[0-9a-f]{64}", self.harness_id)
                and self.harness_id != "harness_pending"
            )
            or any(
                not valid_digest(value)
                for value in (self.prompt_digest, self.executable_digest, self.calculator_digest)
            )
            or (
                not self.model.strip()
                or type(self.timeout_seconds) is not int
                or self.timeout_seconds < 1
                or not math.isfinite(self.max_budget_usd)
                or self.max_budget_usd <= 0
            )
        ):
            raise ValueError("Invalid external agent Harness")


@dataclass(frozen=True, slots=True)
class AgentCaseRecord:
    task_id: str
    harness_id: str
    trial_id: str
    verifier_digest: str
    response: str
    score: float
    input_tokens: int
    output_tokens: int
    cost_usd: float
    duration_ms: int
    transcript_path: str
    transcript_digest: str

    def __post_init__(self) -> None:
        if (
            not self.task_id.strip()
            or not re.fullmatch(r"harness_[0-9a-f]{64}", self.harness_id)
            or not self.trial_id.strip()
            or not valid_digest(self.verifier_digest)
            or not math.isfinite(self.score)
            or not 0 <= self.score <= 1
            or any(
                type(v) is not int or v < 0
                for v in (self.input_tokens, self.output_tokens, self.duration_ms)
            )
            or not math.isfinite(self.cost_usd)
            or self.cost_usd < 0
            or not self.transcript_path.startswith("/")
            or not valid_digest(self.transcript_digest)
        ):
            raise ValueError("Invalid external agent case")


@dataclass(frozen=True, slots=True)
class AgentEvaluationReport:
    harness_id: str
    task_set_id: str
    split: RsiSplit
    model: str
    executable_digest: str
    verifier_digest: str
    trial_id: str
    cases: tuple[AgentCaseRecord, ...]

    def __post_init__(self) -> None:
        if (
            not re.fullmatch(r"harness_[0-9a-f]{64}", self.harness_id)
            or not self.task_set_id.startswith("taskset_")
            or not self.model.strip()
            or not valid_digest(self.executable_digest)
            or not valid_digest(self.verifier_digest)
            or not self.trial_id.strip()
            or not self.cases
            or len({case.task_id for case in self.cases}) != len(self.cases)
            or any(
                case.harness_id != self.harness_id
                or case.trial_id != self.trial_id
                or case.verifier_digest != self.verifier_digest
                for case in self.cases
            )
        ):
            raise ValueError("Invalid external agent evaluation")

    @property
    def score(self) -> float:
        return sum(case.score for case in self.cases) / len(self.cases)
