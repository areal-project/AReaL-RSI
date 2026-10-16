"""Secondary, exact numeric audit; never changes frozen TIR reward or raw scores."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path

from areal_rsi.api.control import decode_record, encode_record
from areal_rsi.api.records import ContentRef, EvaluationResult, EvaluationSample
from areal_rsi.infra.artifacts import file_ref, read_record, write_record
from examples.tir.benchmark import read_examples
from examples.tir.pipeline import local_path


@dataclass(frozen=True, slots=True)
class NumericAudit:
    source: ContentRef
    evaluation: ContentRef
    dataset: ContentRef
    primary_accuracy: float
    numeric_accuracy: float
    format_only_errors: int
    result: EvaluationResult


def boxed_value(output: str) -> Fraction | None:
    starts = tuple(re.finditer(r"\\boxed\s*\{", output))
    if not starts:
        return None
    start = starts[-1].end()
    depth, end = 1, start
    while end < len(output) and depth:
        if output[end] == "{":
            depth += 1
        elif output[end] == "}":
            depth -= 1
        end += 1
    if depth:
        return None
    value = output[start : end - 1].strip()
    # Remove alphabetic units only; never extract an answer from prose/tool output
    # or execute model-produced expressions. Arithmetic expressions stay unscored.
    value = re.sub(r"\\(?:text|mathrm)\{[a-zA-Z $]+\}", "", value)
    value = value.replace(r"\,", "").replace(r"\ ", "").strip().strip("$")
    value = re.sub(r"\s+", "", value)
    fraction = re.fullmatch(r"([+-]?)\\(?:d?frac)\{([+-]?\d+)\}\{([+-]?\d+)\}", value)
    if fraction:
        denominator = int(fraction[3])
        if not denominator:
            return None
        return (-1 if fraction[1] == "-" else 1) * Fraction(int(fraction[2]), denominator)
    if not re.fullmatch(r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", value):
        return None
    try:
        return Fraction(Decimal(value.replace(",", "")))
    except InvalidOperation:
        return None


def audit(evaluation: Path, dataset: Path, output: Path) -> NumericAudit:
    source = read_record(evaluation, EvaluationResult)
    examples = {item.task_id: item for item in read_examples(dataset)}
    with local_path(source.samples).open() as stream:
        samples = [decode_record(EvaluationSample, json.loads(line)) for line in stream]
    if len(samples) != len(examples) or {s.task_id for s in samples} != examples.keys():
        raise ValueError("Numeric audit requires complete, unique task coverage")
    audited = []
    for sample in samples:
        passed = boxed_value(sample.output) == Fraction(int(examples[sample.task_id].answer))
        if sample.passed and not passed:
            raise ValueError("Numeric audit disagrees with an originally correct answer")
        audited.append(replace(sample, passed=passed, score=float(passed)))
    count = len(audited)
    numeric_accuracy = sum(s.passed for s in audited) / count
    primary_accuracy = sum(s.passed for s in samples) / count
    output.mkdir(parents=True, exist_ok=False)
    path = output / "samples.jsonl"
    with path.open("x") as stream:
        for sample in audited:
            stream.write(json.dumps(encode_record(sample), ensure_ascii=False) + "\n")
    result = replace(
        source,
        samples=file_ref(path),
        report=replace(
            source.report,
            score=numeric_accuracy,
            passed=all(s.passed for s in audited),
            metrics={**source.report.metrics, "accuracy": numeric_accuracy},
        ),
    )
    record = NumericAudit(
        file_ref(Path(__file__)),
        file_ref(evaluation),
        file_ref(dataset),
        primary_accuracy,
        numeric_accuracy,
        sum(s.passed for s in audited) - sum(s.passed for s in samples),
        result,
    )
    write_record(output / "audit.json", record)
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    record = audit(args.evaluation, args.dataset, args.output)
    print(
        f"primary={record.primary_accuracy:.4f} numeric={record.numeric_accuracy:.4f} "
        f"format_only_errors={record.format_only_errors}"
    )
