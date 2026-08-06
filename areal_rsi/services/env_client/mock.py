"""Deterministic generic environment client for local tests and demos."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from areal_rsi.schemas.core import EvalJob, EvalRequest, Traj

_DEFAULT_REWARDS: tuple[float, ...] = (1.0, 0.0, 0.5)


class MockEnvClient:
    """Deterministic EnvClient implementation used by local tests and demos."""

    def __init__(self, *, task_set: list[dict[str, Any]] | None = None) -> None:
        """Initialize the mock client with an optional fixed task set."""

        self._job_counts: defaultdict[str, int] = defaultdict(int)
        # When a task set is supplied, emitted trajectory task_ids ARE its entry
        # ids (e.g. real gsm8k instance_ids), so a Researcher experience keyed by
        # task_id lines up with the OPD train data keyed by instance_id. Without
        # it, three synthetic benchmark tasks are emitted as before.
        self._task_set = [dict(entry) for entry in task_set] if task_set else None

    def submit_eval(self, request: EvalRequest) -> EvalJob:
        """Create a deterministic completed mock evaluation job."""

        model_name = _asset_label(request.model, "mock_ckpt")
        benchmark_name = _ref_label(request.benchmark, "mock_benchmark")
        harness_name = _ref_label(request.harness, "mock_harness")
        return self._new_job(
            prefix="eval",
            model_ref={"model_name": model_name, "model_ref": model_name},
            benchmark=benchmark_name,
            harness_config={"harness": harness_name},
            assets={
                "model": dict(request.model),
                "benchmark": _asset_dict(request.benchmark),
                "harness": _asset_dict(request.harness),
                **({"skill": dict(request.skill)} if request.skill is not None else {}),
            },
            job_config=dict(request.job_config),
            metadata=dict(request.metadata),
        )

    def get_eval_progress(self, job: EvalJob) -> dict[str, object]:
        """Return completed progress for a mock evaluation job."""

        return {
            "eval_job_id": job["eval_job_id"],
            "status": job.get("status", "done"),
            "progress": 100,
            "task_counts": {"total": 3, "done": 3},
        }

    def get_eval_result(self, job: EvalJob) -> dict[str, object]:
        """Return aggregate mock metrics for a mock evaluation job."""

        return {
            "eval_job_id": job["eval_job_id"],
            "status": job.get("status", "done"),
            "metrics": dict(job.get("metrics", {})),
        }

    def get_trajs(self, job: EvalJob, include_messages: bool = False) -> list[Traj]:
        """Return deterministic mock trajectories for an evaluation job."""

        benchmark = job.get("benchmark", job.get("task_set", "mock_benchmark"))
        source = job.get("metadata", {}).get("source", "eval")
        model_ref = job.get("model_ref", {}).get("model_ref", "mock_ckpt")
        skill = job.get("assets", {}).get("skill")
        experiences = _task_experience_map(skill)
        distilled = source == "experience_distillation"
        specs = self._task_specs(benchmark, distilled)
        trajs: list[Traj] = []
        for index, (task_id, reward, user_content) in enumerate(specs):
            experience = experiences.get(task_id)
            traj: Traj = {
                "traj_id": f"{job['eval_job_id']}_traj_{index}",
                "task_id": task_id,
                "model_ref": model_ref,
                "harness_id": job.get("harness_config", {}).get("harness", "mock_harness"),
                "reward": reward,
                "status": "done",
                "metadata": {
                    "source": source,
                    "eval_job_id": job["eval_job_id"],
                    "benchmark": benchmark,
                    "skill_applied": bool(experience),
                    **({"experience": experience} if experience else {}),
                },
            }
            if include_messages:
                original_messages = [
                    {"role": "system", "content": "You are a mock agent."},
                    {"role": "user", "content": user_content},
                    {"role": "assistant", "content": "Mock solution and explanation."},
                ]
                traj["metadata"]["original_messages"] = original_messages
                traj["messages"] = [dict(message) for message in original_messages]
                if experience:
                    traj["messages"][1]["content"] += f"\n\n{experience}"
            trajs.append(traj)
        return trajs

    def _task_specs(self, benchmark: str, distilled: bool) -> list[tuple[str, float, str]]:
        """Return (task_id, reward, user_content) per emitted trajectory."""

        if self._task_set is not None:
            specs: list[tuple[str, float, str]] = []
            for index, entry in enumerate(self._task_set):
                task_id = str(entry["task_id"])
                reward = 1.0 if distilled else float(
                    entry.get("reward", _DEFAULT_REWARDS[index % len(_DEFAULT_REWARDS)])
                )
                user_content = str(entry.get("user_content") or f"Solve {task_id}.")
                specs.append((task_id, reward, user_content))
            return specs
        rewards = [1.0, 1.0, 1.0] if distilled else list(_DEFAULT_REWARDS)
        return [
            (f"{benchmark}_task_{index}", reward, f"Solve {benchmark}_task_{index}.")
            for index, reward in enumerate(rewards)
        ]

    def _new_job(
        self,
        *,
        prefix: str,
        model_ref: dict[str, Any],
        benchmark: str,
        harness_config: dict[str, Any],
        assets: dict[str, Any],
        job_config: dict[str, Any],
        metadata: dict[str, Any] | None,
    ) -> EvalJob:
        """Allocate and return a new deterministic mock job payload."""

        self._job_counts[prefix] += 1
        job_id = f"{prefix}_{self._job_counts[prefix]:04d}"
        return {
            "eval_job_id": job_id,
            "status": "done",
            "model_ref": model_ref,
            "benchmark": benchmark,
            "harness_config": harness_config,
            "assets": assets,
            "job_config": job_config,
            "metrics": {"success_rate": 0.5, "num_tasks": 3},
            "metadata": metadata or {},
        }


def _asset_label(config: Mapping[str, Any], default: str) -> str:
    """Return a readable label for an asset config or default."""

    if config.get("ref") is not None:
        if isinstance(config["ref"], Mapping) and config["ref"].get("key") and config["ref"].get("version"):
            return f"{config['ref']['key']}@{config['ref']['version']}"
        return str(config["ref"])
    if config.get("key") is not None and config.get("version") is not None:
        return f"{config['key']}@{config['version']}"
    for name in ("model_name", "runtime_model_name", "model_path", "key", "name", "harness", "benchmark"):
        if config.get(name) is not None:
            return str(config[name])
    return default


def _ref_label(config: Mapping[str, Any] | str, default: str) -> str:
    """Return a readable label for a ref string or asset config."""

    if isinstance(config, str):
        return config
    return _asset_label(config, default)


def _asset_dict(config: Mapping[str, Any] | str) -> dict[str, Any]:
    """Normalize a ref string or mapping to a dictionary payload."""

    return {"ref": config} if isinstance(config, str) else dict(config)


def _task_experience_map(skill: object) -> dict[str, str]:
    """Extract task-id keyed experience strings from a skill spec."""

    if not isinstance(skill, Mapping):
        return {}
    spec = skill.get("spec")
    if not isinstance(spec, Mapping):
        return {}
    records = spec.get("task_experiences", [])
    if not isinstance(records, list):
        return {}
    return {
        str(record["task_id"]): str(record["experience"])
        for record in records
        if isinstance(record, Mapping)
        and record.get("task_id") is not None
        and isinstance(record.get("experience"), str)
        and record["experience"].strip()
    }
