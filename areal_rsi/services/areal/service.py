"""AReaL service contract."""

from __future__ import annotations

from typing import Any, Protocol

from areal_rsi.schemas.core import Dataset, Endpoint, ModelRef, TrainMethod, TrainRun


class AReaLService(Protocol):
    """Hosts rollout endpoints and runs AReaL training jobs."""

    def ensure_rollout_endpoint(self, ckpt: str, config: dict[str, Any] | None = None) -> Endpoint:
        """Start or reuse an AReaL-hosted inference endpoint."""

    def update_rollout_endpoint(self, endpoint: Endpoint, ckpt: str) -> Endpoint:
        """Update an existing rollout endpoint to serve a new checkpoint."""

    def train(
        self,
        base_ckpt: str,
        dataset: Dataset,
        method: TrainMethod,
        config: dict[str, Any],
        *,
        envarena: object | None = None,
        model_ref: ModelRef | None = None,
        harness_config: dict[str, Any] | None = None,
    ) -> TrainRun:
        """Run SFT or OPD training and return checkpoint metadata."""
