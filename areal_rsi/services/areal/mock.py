"""Deterministic AReaL training fake used by local controller tests and demo CLI."""

from __future__ import annotations

from typing import Any

from areal_rsi.schemas.core import Dataset, ModelRef, TrainMethod, TrainRun


class MockAReaLService:
    """In-memory fake for the controller's AReaL training contract."""

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
        del envarena
        examples = dataset.get("examples", [])
        next_ckpt = f"{base_ckpt}-{method}-mock"
        return {
            "train_run_id": f"train_{method}_{base_ckpt}",
            "method": method,
            "base_ckpt": base_ckpt,
            "ckpt": next_ckpt,
            "metrics": {
                "num_examples": len(examples),
                "mock_loss": max(0.01, 1.0 / max(1, len(examples))),
            },
            "metadata": {
                "config": config,
                "model_ref": model_ref,
                "harness_config": harness_config or {},
            },
        }
