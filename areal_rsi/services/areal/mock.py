"""Mock AReaL service implementation."""

from __future__ import annotations

from typing import Any

from areal_rsi.schemas.core import Dataset, Endpoint, ModelRef, TrainMethod, TrainRun


class MockAReaLService:
    """Deterministic AReaL service used by local tests and demos."""

    def ensure_rollout_endpoint(self, ckpt: str, config: dict[str, Any] | None = None) -> Endpoint:
        return {
            "model_ref": ckpt,
            "base_url": f"mock://areal/{ckpt}",
            "api_key": "mock-api-key",
            "protocol": "openai-compatible",
            "metadata": {"config": config or {}},
        }

    def update_rollout_endpoint(self, endpoint: Endpoint, ckpt: str) -> Endpoint:
        updated = dict(endpoint)
        updated["model_ref"] = ckpt
        updated["base_url"] = f"mock://areal/{ckpt}"
        updated["metadata"] = {**updated.get("metadata", {}), "updated_to": ckpt}
        return updated

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
