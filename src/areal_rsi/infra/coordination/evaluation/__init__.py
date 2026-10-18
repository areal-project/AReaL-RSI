"""Run task-specific evaluators and retain comparable, complete evidence."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from areal_rsi.api.control import decode_record
from areal_rsi.api.execution import JobSpec
from areal_rsi.api.records import (
    BenchmarkSplit,
    EvaluationReport,
    EvaluationResult,
    EvaluationSample,
    PairedComparison,
    Trajectory,
    TrajectoryRef,
)
from areal_rsi.infra.artifacts import file_ref, local_path, write_record
from areal_rsi.infra.execution import JobExecutor
from areal_rsi.infra.trajectories import LocalTrajectoryStore


def _metrics(samples: tuple[EvaluationSample, ...]) -> dict[str, float]:
    names = set(samples[0].metrics)
    if any(set(sample.metrics) != names for sample in samples):
        raise ValueError("Evaluation samples have inconsistent metric names")
    count = len(samples)
    return {
        **{name: sum(sample.metrics[name] for sample in samples) / count for name in names},
        "accuracy": sum(sample.passed for sample in samples) / count,
        "num_tasks": float(count),
    }


def read_evaluation_samples(
    result: EvaluationResult,
    *,
    task_ids: tuple[str, ...] | None = None,
    solution_id: str | None = None,
    split: BenchmarkSplit | None = None,
) -> tuple[EvaluationSample, ...]:
    """Verify persisted evidence before reusing an aggregate or comparing models."""
    with local_path(result.samples).open() as stream:
        samples = tuple(decode_record(EvaluationSample, json.loads(line)) for line in stream)
    actual = {sample.task_id for sample in samples}
    if not samples or len(actual) != len(samples):
        raise ValueError("Incomplete evaluation: empty or duplicate task identities")
    if task_ids is not None and (len(task_ids) != len(set(task_ids)) or actual != set(task_ids)):
        raise ValueError("Incomplete evaluation: tasks differ from the requested dataset")
    report = result.report
    if (solution_id is not None and report.solution_id != solution_id) or (
        split is not None and report.split != split
    ):
        raise ValueError("Evaluation belongs to a different solution or split")
    expected = _metrics(samples)
    if (
        report.passed != all(sample.passed for sample in samples)
        or not math.isclose(report.score, sum(sample.score for sample in samples) / len(samples))
        or report.metrics.keys() != expected.keys()
        or any(not math.isclose(report.metrics[name], value) for name, value in expected.items())
    ):
        raise ValueError("Evaluation report does not match its sample evidence")
    return samples


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
        with path.open() as stream:
            samples = tuple(decode_record(EvaluationSample, json.loads(line)) for line in stream)
        actual = [sample.task_id for sample in samples]
        if len(actual) != len(set(actual)) or set(actual) != set(task_ids):
            raise ValueError(
                f"Incomplete evaluation in {path}: missing, duplicate or unexpected tasks"
            )
        metrics = _metrics(samples)
        created_at = datetime.now(UTC).isoformat()

        async def persist(sample: EvaluationSample) -> TrajectoryRef:
            digest = hashlib.sha256(f"{run_id}:{name}:{sample.task_id}".encode()).hexdigest()
            content = await asyncio.to_thread(
                write_record, directory / "traces" / f"{digest}.json", sample
            )
            return await self.trajectories.save(
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

        references = []
        # Bound pending tasks and filesystem pressure even for large probe jobs.
        # Drain each batch on an I/O failure before returning without a report.
        for offset in range(0, len(samples), 8):
            batch = await asyncio.gather(
                *(persist(sample) for sample in samples[offset : offset + 8]),
                return_exceptions=True,
            )
            for reference in batch:
                if isinstance(reference, BaseException):
                    raise reference
                references.append(reference)
        count = len(samples)
        report = EvaluationReport(
            solution_id,
            split,
            all(sample.passed for sample in samples),
            sum(sample.score for sample in samples) / count,
            metrics,
            created_at,
        )
        result = EvaluationResult(job.job_id, report, file_ref(path), tuple(references))
        write_record(directory / "evaluation.json", result)
        return result


@dataclass(frozen=True, slots=True)
class _PairedBlock:
    difference: int
    count: int


def paired(
    baseline: EvaluationResult,
    candidate: EvaluationResult,
    *,
    clusters: dict[str, str] | None = None,
) -> PairedComparison:
    def samples(result: EvaluationResult) -> dict[str, EvaluationSample]:
        return {item.task_id: item for item in read_evaluation_samples(result)}

    left, right = samples(baseline), samples(candidate)
    if not left or left.keys() != right.keys():
        raise ValueError("Paired comparison requires exactly the same tasks")
    if clusters is not None and clusters.keys() != left.keys():
        raise ValueError("Question clusters must cover exactly the paired tasks")
    differences = [int(right[key].passed) - int(left[key].passed) for key in sorted(left)]
    wins, losses = differences.count(1), differences.count(-1)
    both_correct = sum(left[key].passed and right[key].passed for key in left)
    n = len(left)
    grouped: dict[str, list[int]] = {}
    for key, difference in zip(sorted(left), differences, strict=True):
        group = clusters[key] if clusters is not None else key
        grouped.setdefault(group, []).append(difference)
    blocks = tuple(_PairedBlock(sum(values), len(values)) for values in grouped.values())
    rng = random.Random(20261017)
    bootstrap = []
    for _ in range(10000):
        draw = rng.choices(blocks, k=len(blocks))
        bootstrap.append(sum(item.difference for item in draw) / sum(item.count for item in draw))
    bootstrap.sort()
    discordant = wins + losses
    p = (
        min(
            1.0,
            2 * sum(math.comb(discordant, k) for k in range(min(wins, losses) + 1)) / 2**discordant,
        )
        if discordant
        else 1.0
    )
    return PairedComparison(
        n,
        sum(item.passed for item in left.values()) / n,
        sum(item.passed for item in right.values()) / n,
        wins,
        losses,
        both_correct,
        n - both_correct - wins - losses,
        (wins - losses) / n,
        bootstrap[249],
        bootstrap[9749],
        p if len(blocks) == n else None,
        len(blocks),
    )
