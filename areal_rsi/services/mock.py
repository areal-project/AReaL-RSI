"""Mock service implementations for the local M1 loop."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from areal_rsi.schemas.core import Dataset, Endpoint, EvalJob, MemoryContext, ModelRef, TrainMethod, TrainRun, Traj


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


class MockEnvArenaClient:
    """Deterministic EnvArena client used by local tests and demos."""

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

    def submit_eval(
        self,
        model_ref: ModelRef,
        task_set: str,
        harness_config: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> EvalJob:
        return self._new_job(
            prefix="eval",
            model_ref=model_ref,
            task_set=task_set,
            harness_config=harness_config,
            metadata=metadata,
        )

    def submit_rollout(
        self,
        model_ref: ModelRef,
        tasks: list[dict[str, Any]],
        harness_config: dict[str, Any],
        source: str,
        metadata: dict[str, Any] | None = None,
    ) -> EvalJob:
        job = self._new_job(
            prefix="rollout",
            model_ref=model_ref,
            task_set="custom_tasks",
            harness_config=harness_config,
            metadata={**(metadata or {}), "source": source, "tasks": tasks},
        )
        job["num_tasks"] = len(tasks)
        return job

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


class MockResearchAgent:
    """Deterministic ResearchAgent used by local tests and demos."""

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
        filtered = _filter_trajs(trajs, traj_filter or {})
        examples = [
            {
                "task": {
                    "task_id": traj["task_id"],
                    "harness_id": traj.get("harness_id"),
                },
                "experience": _experience_for_traj(traj),
                "source_traj_ids": [traj["traj_id"]],
                "metadata": {
                    "reward": traj.get("reward"),
                    "current_ckpt": current_ckpt,
                    "generation_method": "mock_rule_based",
                },
            }
            for traj in filtered
        ]
        return {
            "dataset_id": f"dataset_{current_ckpt}_{len(examples)}",
            "examples": examples,
            "metadata": {
                "source_num_trajs": len(trajs),
                "filtered_num_trajs": len(filtered),
                "memory_events": len(memory_context.get("events", [])),
                "config": config or {},
            },
        }

    def choose_ckpt(self, train_runs: list[TrainRun], memory_context: MemoryContext) -> str:
        if not train_runs:
            raise ValueError("No train runs available for checkpoint selection.")
        return str(train_runs[-1]["ckpt"])


class MockEvalFirewall:
    """Simple metric-delta evaluator for local tests and demos."""

    def summarize(self, eval_result: dict[str, Any], *, policy: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "metrics": eval_result.get("metrics", {}),
            "decision": "informational",
            "risks": [],
            "metadata": {"policy": policy or {}},
        }

    def decide(self, baseline: dict[str, Any], candidate: dict[str, Any], gate: dict[str, Any]) -> dict[str, Any]:
        metric = gate.get("metric", "success_rate")
        min_delta = float(gate.get("min_delta", 0.0))
        baseline_score = float(baseline.get("metrics", {}).get(metric, 0.0))
        candidate_score = float(candidate.get("metrics", {}).get(metric, 0.0))
        delta = candidate_score - baseline_score
        return {
            "accepted": delta >= min_delta,
            "metric": metric,
            "baseline": baseline_score,
            "candidate": candidate_score,
            "delta": delta,
            "risks": [] if delta >= min_delta else ["metric_gate_not_met"],
        }


def _filter_trajs(trajs: list[Traj], traj_filter: dict[str, Any]) -> list[Traj]:
    reward_lt = traj_filter.get("reward_lt")
    if reward_lt is None:
        return list(trajs)
    return [traj for traj in trajs if float(traj.get("reward", 0.0)) < float(reward_lt)]


def _experience_for_traj(traj: Traj) -> str:
    reward = float(traj.get("reward", 0.0))
    if reward >= 1.0:
        return "Preserve the successful solution pattern and avoid unnecessary changes."
    if reward <= 0.0:
        return "Use task evidence and failing feedback to localize the fix before patching."
    return "Refine near-miss solutions by checking tests and limiting patch scope."
