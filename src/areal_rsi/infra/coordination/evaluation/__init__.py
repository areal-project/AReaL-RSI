"""Run task-specific evaluators and retain comparable, complete evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from areal_rsi.api.control import decode_record
from areal_rsi.api.execution import JobSpec
from areal_rsi.api.records import (
    BenchmarkSplit,
    EvaluationReport,
    EvaluationResult,
    EvaluationSample,
    Trajectory,
)
from areal_rsi.infra.artifacts import file_ref, write_record
from areal_rsi.infra.execution import JobExecutor
from areal_rsi.infra.trajectories import LocalTrajectoryStore


class EvaluationService:
    def __init__(self, jobs: JobExecutor, trajectories: LocalTrajectoryStore):
        self.jobs = jobs
        self.trajectories = trajectories

    async def evaluate(
        self,
        run_id: str,
        name: str,
        spec: JobSpec,
        *,
        task_ids: tuple[str, ...],
        solution_id: str,
        split: BenchmarkSplit = BenchmarkSplit.VALIDATE,
    ) -> EvaluationResult:
        if not task_ids or len(set(task_ids)) != len(task_ids):
            raise ValueError("Evaluation requires unique, nonempty expected task IDs")
        job = await self.jobs.run(run_id, name, spec)
        directory = Path(job.result_path)
        path = directory / "samples.jsonl"
        samples = [
            decode_record(EvaluationSample, json.loads(line))
            for line in path.read_text().splitlines()
        ]
        actual = [sample.task_id for sample in samples]
        if len(actual) != len(set(actual)) or set(actual) != set(task_ids):
            raise ValueError(
                f"Incomplete evaluation in {path}: missing, duplicate or unexpected tasks"
            )
        metric_names = set(samples[0].metrics)
        if any(set(sample.metrics) != metric_names for sample in samples):
            raise ValueError("Evaluation samples have inconsistent metric names")
        created_at = datetime.now(UTC).isoformat()
        references = []
        for sample in samples:
            digest = hashlib.sha256(f"{run_id}:{name}:{sample.task_id}".encode()).hexdigest()
            content = write_record(directory / "traces" / f"{digest}.json", sample)
            references.append(
                await self.trajectories.save(
                    Trajectory(
                        f"trace-{digest}",
                        name,
                        content,
                        {},
                        created_at,
                        task_id=sample.task_id,
                        solution_id=solution_id,
                    )
                )
            )
        count = len(samples)
        report = EvaluationReport(
            solution_id,
            split,
            all(sample.passed for sample in samples),
            sum(sample.score for sample in samples) / count,
            {
                **{
                    name: sum(sample.metrics[name] for sample in samples) / count
                    for name in metric_names
                },
                "accuracy": sum(sample.passed for sample in samples) / count,
                "num_tasks": float(count),
            },
            created_at,
        )
        result = EvaluationResult(job.job_id, report, file_ref(path), tuple(references))
        write_record(directory / "evaluation.json", result)
        return result
