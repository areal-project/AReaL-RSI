"""EnvArena client contract."""

from __future__ import annotations

from typing import Any, Protocol

from areal_rsi.schemas.core import Endpoint, EvalJob, ModelRef, Traj, TrajSource


class EnvArenaClient(Protocol):
    """Registers model endpoints, launches eval/rollout jobs, and fetches trajectories."""

    def register_model(self, endpoint: Endpoint) -> ModelRef:
        """Register an AReaL inference endpoint in EnvArena."""

    def update_model(self, model_ref: ModelRef, endpoint: Endpoint) -> ModelRef:
        """Update an EnvArena model registration after endpoint or checkpoint changes."""

    def submit_eval(
        self,
        model_ref: ModelRef,
        task_set: str,
        harness_config: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> EvalJob:
        """Submit an evaluation job."""

    def submit_rollout(
        self,
        model_ref: ModelRef,
        tasks: list[dict[str, Any]],
        harness_config: dict[str, Any],
        source: TrajSource,
        metadata: dict[str, Any] | None = None,
    ) -> EvalJob:
        """Submit a rollout job used by ResearchAgent validation or OPD."""

    def get_trajs(self, job: EvalJob, include_messages: bool = False) -> list[Traj]:
        """Fetch trajectories produced by an eval or rollout job."""
