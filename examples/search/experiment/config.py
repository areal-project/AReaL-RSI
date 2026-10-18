"""Typed configuration shared by the host orchestrator and GPU workers."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Literal

from areal_rsi.api.execution import AReaLRuntime


@dataclass(frozen=True, slots=True)
class TrainingConfig:
    batch_size: int = 8
    n_samples: int = 4
    max_steps: int = 10
    epochs: int = 1
    learning_rate: float = 1e-6
    seed: int = 17

    def __post_init__(self) -> None:
        if min(self.batch_size, self.max_steps, self.epochs) < 1 or self.n_samples < 2:
            raise ValueError("GRPO requires positive steps/batches and at least two samples")
        if not 0 < self.learning_rate < 0.001:
            raise ValueError("Invalid model learning rate")

    def validate_dataset_size(self, questions: int) -> None:
        available_steps = self.epochs * (questions // self.batch_size)
        if available_steps < self.max_steps:
            raise ValueError(
                f"Dataset and epochs allow {available_steps} full updates; "
                f"requested {self.max_steps}. Increase epochs or provide more training questions."
            )


@dataclass(frozen=True, slots=True)
class HarnessConfig:
    max_model_calls: int = 50
    max_input_tokens: int = 115000
    max_completion_tokens: int = 10000
    context_tokens: int = 131072
    temperature: float = 0.7
    top_p: float = 0.8
    top_k: int = 20
    thinking: bool = True

    def __post_init__(self) -> None:
        expected = (50, 115000, 10000, 131072, 0.7, 0.8, 20, True)
        actual = (
            self.max_model_calls,
            self.max_input_tokens,
            self.max_completion_tokens,
            self.context_tokens,
            self.temperature,
            self.top_p,
            self.top_k,
            self.thinking,
        )
        if actual != expected:
            raise ValueError("The primary model comparison requires the frozen original harness")


@dataclass(frozen=True, slots=True)
class SearchConfig:
    runtime: AReaLRuntime
    model: str
    benchmark_repository: str
    dataset_directory: str
    index_directory: str
    embedding_model: str
    snippet_tokenizer: str
    output_root: str
    training: TrainingConfig = field(default_factory=TrainingConfig)
    harness: HarnessConfig = field(default_factory=HarnessConfig)
    actor_gpus: int = 4
    rollout_gpus: int = 2
    max_concurrent_rollouts: int = 8
    evaluation_seed: int = 42
    timeout_seconds: float = 7200.0
    dense_python: str = sys.executable
    project: str = ""

    def __post_init__(self) -> None:
        if min(self.actor_gpus, self.rollout_gpus, self.max_concurrent_rollouts) < 1:
            raise ValueError("Actor, rollout and concurrency allocations must be positive")
        if self.actor_gpus + self.rollout_gpus + 1 > 8:
            raise ValueError("Actor + rollout + dense retrieval must fit within eight GPUs")


@dataclass(frozen=True, slots=True)
class WorkerRequest:
    mode: Literal["train", "eval"]
    config: SearchConfig
    model: str
    dataset: str
    mcp_url: str
    output: str
    trial: str
