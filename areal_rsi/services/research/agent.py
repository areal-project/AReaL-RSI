"""ResearchAgent service contract."""

from __future__ import annotations

from typing import Any, Protocol

from areal_rsi.schemas.core import Dataset, MemoryContext, ModelRef, TrainRun, Traj


class ResearchAgent(Protocol):
    """Worker agent that converts EnvArena trajectories into training datasets."""

    def generate_dataset(
        self,
        trajs: list[Traj],
        memory_context: MemoryContext,
        current_ckpt: str,
        *,
        envarena: object | None = None,
        model_ref: ModelRef | None = None,
        harness_config: dict[str, Any] | None = None,
        traj_filter: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
    ) -> Dataset:
        """Generate task experiences or a distilled dataset, as selected by config."""

    def choose_ckpt(self, train_runs: list[TrainRun], memory_context: MemoryContext) -> str:
        """Choose the checkpoint for the next iteration in the current Run."""
