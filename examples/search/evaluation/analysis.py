"""Paired statistics for complete, independently judged benchmark evaluations."""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PairedReport:
    questions: int
    baseline_correct: int
    candidate_correct: int
    baseline_accuracy: float
    candidate_accuracy: float
    candidate_wins: int
    candidate_losses: int
    delta_percentage_points: float
    delta_bootstrap_95_percentage_points: tuple[float, float]
    exact_mcnemar_p: float
    bootstrap_repetitions: int
    bootstrap_seed: int


def read_official_results(directory: Path, expected: set[str]) -> dict[str, bool]:
    """Reject parse/coverage failures instead of silently shrinking a denominator."""
    result: dict[str, bool] = {}
    for path in directory.glob("*_eval.json"):
        record = json.loads(path.read_text())
        question_id = str(record["query_id"])
        if question_id in result:
            raise ValueError(f"Duplicate judged query: {question_id}")
        judge = record["judge_result"]
        # The unmodified official evaluator omits `correct` for its explicit
        # no-answer skip. This is a model/budget failure in the full denominator,
        # unlike an attempted judge response that could not be parsed.
        no_answer_skip = (
            judge.get("parse_error") is True
            and judge.get("error") == "Response incomplete or cannot be parsed"
            and record.get("judge_prompt") is None
            and not record.get("judge_response")
            and (record["is_completed"] is False or record.get("response") == "")
        )
        if no_answer_skip:
            if "correct" in judge and judge["correct"] is not False:
                raise ValueError(
                    f"Invalid correctness on an official no-answer skip: {question_id}"
                )
            result[question_id] = False
            continue
        if record["is_completed"] and (
            judge.get("parse_error") or not record.get("judge_response")
        ):
            raise ValueError(f"Judge failed to score query: {question_id}")
        correct = judge.get("correct")
        if type(correct) is not bool:
            raise ValueError(f"Non-boolean correctness for query: {question_id}")
        result[question_id] = correct
    if not expected or set(result) != expected:
        raise ValueError("Benchmark evaluation has missing or unexpected queries")
    return result


def paired_statistics(
    baseline: dict[str, bool],
    candidate: dict[str, bool],
    *,
    repetitions: int = 20000,
    seed: int = 20261017,
) -> PairedReport:
    if not baseline or set(baseline) != set(candidate):
        raise ValueError("Paired comparisons require the same nonempty query set")
    if repetitions < 1000:
        raise ValueError("Use at least 1000 paired bootstrap repetitions")
    differences = [int(candidate[key]) - int(baseline[key]) for key in sorted(baseline)]
    n = len(differences)
    wins, losses = differences.count(1), differences.count(-1)
    discordant = wins + losses
    probability = (
        min(
            1.0,
            2 * sum(math.comb(discordant, k) for k in range(min(wins, losses) + 1)) / 2**discordant,
        )
        if discordant
        else 1.0
    )
    rng = random.Random(seed)
    # Resample query-level paired differences, preserving within-query dependence.
    bootstrap = sorted(100 * sum(rng.choices(differences, k=n)) / n for _ in range(repetitions))
    interval = (bootstrap[int(0.025 * repetitions)], bootstrap[int(0.975 * repetitions)])
    base_correct, candidate_correct = sum(baseline.values()), sum(candidate.values())
    return PairedReport(
        n,
        base_correct,
        candidate_correct,
        base_correct / n,
        candidate_correct / n,
        wins,
        losses,
        100 * (candidate_correct - base_correct) / n,
        interval,
        probability,
        repetitions,
        seed,
    )


def wilson_interval(correct: int, questions: int) -> tuple[float, float]:
    if not 0 <= correct <= questions or questions < 1:
        raise ValueError("Invalid binomial sample")
    z = 1.959963984540054
    p = correct / questions
    denominator = 1 + z * z / questions
    center = (p + z * z / (2 * questions)) / denominator
    half = z * math.sqrt(p * (1 - p) / questions + z * z / (4 * questions**2)) / denominator
    return max(0.0, center - half), min(1.0, center + half)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    with arguments.queries.open() as source:
        identifiers = [row[0] for row in csv.reader(source, delimiter="\t")]
    expected = set(identifiers)
    if len(expected) != 830 or len(identifiers) != 830:
        raise ValueError("Final BrowseComp-Plus comparison requires all 830 unique queries")
    report = paired_statistics(
        read_official_results(arguments.baseline, expected),
        read_official_results(arguments.candidate, expected),
    )
    output = {
        **asdict(report),
        "candidate_wilson_95": wilson_interval(report.candidate_correct, report.questions),
        "main_seed_accuracy_target_met": report.candidate_correct >= 125,
        "scope": "Single-seed paired accuracy only; not the complete research-goal audit",
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(output, indent=2), flush=True)


if __name__ == "__main__":
    main()
