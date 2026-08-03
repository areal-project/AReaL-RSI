"""Build task-scoped EnvArena skills from Researcher experiences."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from typing import Any


def experience_records_from_result(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Normalize completed task experiences from file-backed or legacy results."""

    records = result.get("experience_records")
    if isinstance(records, list):
        return _completed_records(records)

    dataset = result.get("dataset", result)
    examples = dataset.get("examples", []) if isinstance(dataset, Mapping) else []
    normalized: list[dict[str, Any]] = []
    if isinstance(examples, list):
        for example in examples:
            if not isinstance(example, Mapping):
                continue
            task = example.get("task", {})
            task_id = (
                task.get("task_id") if isinstance(task, Mapping) else example.get("task_id")
            )
            experience = example.get("experience")
            if task_id is None or not isinstance(experience, str) or not experience.strip():
                continue
            normalized.append(
                {
                    "schema_version": "task-experience.v1",
                    "task_id": str(task_id),
                    "status": "completed",
                    "source_traj_ids": list(example.get("source_traj_ids", [])),
                    "experience": experience.strip(),
                    "analysis": str(example.get("analysis", "")),
                    "training_recommendation": dict(
                        example.get("training_recommendation", {})
                    ),
                    "metadata": dict(example.get("metadata", {})),
                }
            )
    return normalized


def build_experience_skill(
    experiences: Iterable[Mapping[str, Any]],
    *,
    run_id: str,
    iteration: int,
) -> dict[str, Any]:
    """Return a new Arena skill asset that appends one experience per task."""

    by_task: dict[str, dict[str, Any]] = {}
    for record in experiences:
        if (
            record.get("task_id") is None
            or not isinstance(record.get("experience"), str)
            or not str(record["experience"]).strip()
            or record.get("status", "completed") != "completed"
        ):
            continue
        task_id = str(record["task_id"])
        by_task[task_id] = {
            "task_id": task_id,
            "experience": str(record["experience"]).strip(),
            "source_traj_ids": [
                str(item) for item in record.get("source_traj_ids", [])
            ],
        }
    task_experiences = [by_task[task_id] for task_id in sorted(by_task)]
    if not task_experiences:
        raise ValueError("Researcher produced no completed task experiences for the Arena skill.")

    digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:10]
    safe_run = re.sub(r"[^a-z0-9-]+", "-", run_id.lower()).strip("-")[-24:]
    key = f"rsi-exp-{safe_run or digest}-i{iteration:03d}-{digest}"
    name = f"RSI experiences for {run_id} iteration {iteration}"
    return {
        "type": "new",
        "key": key,
        "version": "1.0.0",
        "name": name,
        "spec": {
            "schema_version": "rsi-experience-skill.v1",
            "manifest": {
                "name": name,
                "description": (
                    "Task-specific Researcher experiences appended to the user prompt "
                    "for trajectory distillation."
                ),
            },
            "injection": {
                "target_role": "user",
                "position": "append",
                "selection_key": "task_id",
            },
            "task_experiences": task_experiences,
        },
        "tags": ["areal-rsi", "experience-distillation"],
        "metadata": {"run_id": run_id, "iteration": iteration},
        "publish": True,
    }


def task_experience_map(skill: Mapping[str, Any] | None) -> dict[str, str]:
    """Extract the task-to-experience mapping used by the EnvArena mock."""

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


def _completed_records(records: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, Mapping) or record.get("status") != "completed":
            continue
        experience = record.get("experience")
        if record.get("task_id") is None or not isinstance(experience, str) or not experience.strip():
            continue
        normalized.append(dict(record))
    return normalized
