"""Deterministic generic environment client for local tests and demos."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from areal_rsi.schemas.core import Endpoint, EvalJob, EvalRequest, ModelRef, Traj


class MockEnvClient:
    """Deterministic EnvClient implementation used by local tests and demos."""

    def __init__(self) -> None:
        self._job_counts: defaultdict[str, int] = defaultdict(int)

    def register_model(self, endpoint: Endpoint) -> ModelRef:
        model_ref = endpoint.get("model_ref", "unknown")
        return {
            "env_model_id": f"env_{model_ref}",
            "model_ref": model_ref,
            "endpoint": endpoint,
            "metadata": {"registered_by": "mock"},
        }

    def update_model(self, model_ref: ModelRef, endpoint: Endpoint) -> ModelRef:
        updated = dict(model_ref)
        updated["model_ref"] = endpoint.get("model_ref", updated.get("model_ref"))
        updated["endpoint"] = endpoint
        updated["metadata"] = {**updated.get("metadata", {}), "updated_by": "mock"}
        return updated

    def submit_eval(self, request: EvalRequest) -> EvalJob:
        return self._new_job(
            prefix="eval",
            model_ref=request.model_ref,
            task_set=request.task_set,
            harness_config=dict(request.harness_config),
            metadata=dict(request.metadata),
        )

    def get_eval_progress(self, job: EvalJob) -> dict[str, object]:
        return {
            "eval_job_id": job["eval_job_id"],
            "status": job.get("status", "done"),
            "progress": 100,
            "task_counts": {"total": 3, "done": 3},
        }

    def get_eval_result(self, job: EvalJob) -> dict[str, object]:
        return {
            "eval_job_id": job["eval_job_id"],
            "status": job.get("status", "done"),
            "metrics": dict(job.get("metrics", {})),
        }

    def get_trajs(self, job: EvalJob, include_messages: bool = False) -> list[Traj]:
        task_set = job.get("task_set", "mock_tasks")
        source = job.get("metadata", {}).get("source", "eval")
        model_ref = job.get("model_ref", {}).get("model_ref", "mock_ckpt")
        rewards = [1.0, 0.0, 0.5]
        trajs: list[Traj] = []
        for index, reward in enumerate(rewards):
            traj: Traj = {
                "traj_id": f"{job['eval_job_id']}_traj_{index}",
                "task_id": f"{task_set}_task_{index}",
                "model_ref": model_ref,
                "harness_id": job.get("harness_config", {}).get("harness", "mock_harness"),
                "reward": reward,
                "status": "done",
                "metadata": {
                    "source": source,
                    "eval_job_id": job["eval_job_id"],
                    "task_set": task_set,
                },
            }
            if include_messages:
                traj["messages"] = [
                    {"role": "system", "content": "You are a mock SWE agent."},
                    {"role": "user", "content": f"Solve {traj['task_id']}."},
                    {"role": "assistant", "content": "Mock patch and explanation."},
                ]
            trajs.append(traj)
        return trajs

    def _new_job(
        self,
        *,
        prefix: str,
        model_ref: ModelRef,
        task_set: str,
        harness_config: dict[str, Any],
        metadata: dict[str, Any] | None,
    ) -> EvalJob:
        self._job_counts[prefix] += 1
        job_id = f"{prefix}_{self._job_counts[prefix]:04d}"
        return {
            "eval_job_id": job_id,
            "status": "done",
            "model_ref": model_ref,
            "task_set": task_set,
            "harness_config": harness_config,
            "metrics": {"success_rate": 0.5, "num_tasks": 3},
            "metadata": metadata or {},
        }
