"""prompt-specific trajectory evidence carried by the example recipe."""

from __future__ import annotations

import math
from dataclasses import dataclass

from areal_rsi.api.records import RsiSplit, valid_digest


@dataclass(frozen=True, slots=True)
class Trajectory:
    task_id: str
    input_ids: tuple[int, ...]
    loss_mask: tuple[int, ...]
    attention_mask: tuple[int, ...]
    reward: float

    def __post_init__(self) -> None:
        if (
            not self.task_id.strip()
            or not self.input_ids
            or not len(self.input_ids) == len(self.loss_mask) == len(self.attention_mask)
            or any(type(token) is not int or token < 0 for token in self.input_ids)
            or any(
                type(value) is not int or value not in (0, 1)
                for value in self.loss_mask + self.attention_mask
            )
            or any(
                self.attention_mask[i] < self.attention_mask[i + 1]
                for i in range(len(self.attention_mask) - 1)
            )
            or not math.isfinite(self.reward)
            or not 0 <= self.reward <= 1
        ):
            raise ValueError("prompt trajectory has invalid token masks or reward")


@dataclass(frozen=True, slots=True)
class TraceBundle:
    version: int
    task_set_id: str
    split: RsiSplit
    model_digest: str
    harness_id: str
    runner_digest: str
    verifier_digest: str
    trajectories: tuple[Trajectory, ...]

    def __post_init__(self) -> None:
        if (
            self.version != 1
            or not self.task_set_id.startswith("taskset_")
            or not self.harness_id.startswith("harness_")
            or any(
                not valid_digest(value)
                for value in (self.model_digest, self.runner_digest, self.verifier_digest)
            )
            or not self.trajectories
            or len({item.task_id for item in self.trajectories}) != len(self.trajectories)
        ):
            raise ValueError("prompt trace bundle has invalid lineage or coverage")
