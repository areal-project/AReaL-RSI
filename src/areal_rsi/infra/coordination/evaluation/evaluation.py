"""Trusted prompt trajectory decoding and complete, comparable RSI reports."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Sequence
from typing import Any

from areal_rsi.api.control import json_bytes
from areal_rsi.api.records import RsiCaseScore, RsiEvaluationReport, RsiSplit, TaskSetVersion
from areal_rsi.api.research import RsiTrainingSignal
from areal_rsi.infra.coordination.evaluation.contracts import Trajectory


def assess_training_signal(
    primary: RsiEvaluationReport,
    repeat: RsiEvaluationReport,
    *,
    minimum_mixed_cases: int,
) -> RsiTrainingSignal:
    """Measure within-task reward variation before starting GRPO."""

    if primary.split is not RsiSplit.HARNESS_EVOLVE or repeat.split is not primary.split:
        raise ValueError("GRPO signal must use the frozen harness_evolve split")
    for field in (
        "model_digest",
        "harness_id",
        "task_set_id",
        "runner_digest",
        "verifier_digest",
        "temperature",
        "max_new_tokens",
    ):
        if getattr(primary, field) != getattr(repeat, field):
            raise ValueError(f"GRPO signal comparison changed {field}")
    if primary.seed == repeat.seed or tuple(case.task_id for case in primary.cases) != tuple(
        case.task_id for case in repeat.cases
    ):
        raise ValueError("GRPO signal comparison needs the same tasks and distinct seeds")
    if type(minimum_mixed_cases) is not int or not 1 <= minimum_mixed_cases <= len(primary.cases):
        raise ValueError("GRPO signal threshold must fit the frozen evolve task count")
    if any(case.score not in (0.0, 1.0) for report in (primary, repeat) for case in report.cases):
        raise ValueError("prompt GRPO signal requires binary task rewards")
    mixed = sum(
        first.score != second.score
        for first, second in zip(primary.cases, repeat.cases, strict=True)
    )
    return RsiTrainingSignal(
        primary.model_digest,
        primary.harness_id,
        primary.task_set_id,
        "sha256:" + hashlib.sha256(json_bytes(primary)).hexdigest(),
        "sha256:" + hashlib.sha256(json_bytes(repeat)).hexdigest(),
        primary.seed,
        repeat.seed,
        mixed,
        len(primary.cases),
        minimum_mixed_cases,
        mixed >= minimum_mixed_cases,
    )


def _list(value: Any) -> Any:
    return value.tolist() if hasattr(value, "tolist") else value


def _row(value: Any, name: str) -> list[Any]:
    rows = _list(value)
    if not isinstance(rows, list) or len(rows) != 1:
        raise ValueError(f"prompt {name} must have exactly one sample row")
    row = _list(rows[0])
    if not isinstance(row, list):
        raise ValueError(f"prompt {name} row must be a list")
    return row


def verified_case(
    task_id: str,
    trajectory: dict[str, Any],
    *,
    answer: str,
    decode: Callable[[list[int]], str],
    verify: Callable[[str, str], float],
) -> RsiCaseScore:
    """Recompute reward from generated tokens, excluding tool observations."""

    evidence = decode_trajectory(task_id, trajectory)
    output_ids = [
        token
        for token, trainable, visible in zip(
            evidence.input_ids, evidence.loss_mask, evidence.attention_mask, strict=True
        )
        if trainable and visible
    ]
    response = decode(output_ids)
    score = float(verify(response, answer))
    if not math.isfinite(score) or not math.isclose(score, evidence.reward, abs_tol=1e-6):
        raise ValueError("prompt trusted verifier disagrees with rollout reward")
    return RsiCaseScore(task_id, score, response, len(output_ids))


def decode_trajectory(task_id: str, trajectory: dict[str, Any]) -> Trajectory:
    """Keep the exact token/mask evidence needed to recheck tool boundaries."""

    ids = _row(trajectory.get("input_ids"), "input_ids")
    loss_mask = _row(trajectory.get("loss_mask"), "loss_mask")
    attention = _row(trajectory.get("attention_mask"), "attention_mask")
    if any(
        type(value) not in (int, bool) or value not in (0, 1) for value in loss_mask + attention
    ):
        raise ValueError("prompt trajectory masks must be binary")
    rewards = _list(trajectory.get("rewards"))
    if (
        not isinstance(rewards, list)
        or len(rewards) != 1
        or type(rewards[0]) not in (int, float)
        or not math.isfinite(float(rewards[0]))
    ):
        raise ValueError("prompt trajectory reward is invalid")
    return Trajectory(
        task_id,
        tuple(ids),
        tuple(int(value) for value in loss_mask),
        tuple(int(value) for value in attention),
        float(rewards[0]),
    )


def complete_report(
    task_set: TaskSetVersion,
    split: RsiSplit,
    cases: Sequence[RsiCaseScore],
    *,
    model_digest: str,
    harness_id: str,
    runner_digest: str,
    verifier_digest: str,
    seed: int,
    temperature: float,
    max_new_tokens: int,
    trajectory_path: str,
    trajectory_digest: str,
) -> RsiEvaluationReport:
    expected = getattr(task_set, split.value).task_ids
    by_id = {case.task_id: case for case in cases}
    if len(by_id) != len(cases) or set(by_id) != set(expected):
        raise ValueError("prompt evaluation must cover every frozen task exactly once")
    return RsiEvaluationReport(
        model_digest,
        harness_id,
        task_set.task_set_id,
        split,
        runner_digest,
        verifier_digest,
        seed,
        temperature,
        max_new_tokens,
        tuple(by_id[task_id] for task_id in expected),
        trajectory_path,
        trajectory_digest,
    )
