"""Secondary numeric scoring keeps raw results and diagnoses format-only gains."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest
from test_training import command

from areal_rsi.api.control import encode_record
from areal_rsi.api.records import EvaluationSample, TraceEvent
from areal_rsi.infra.artifacts import file_ref, read_record, write_record
from areal_rsi.infra.coordination.evaluation import EvaluationService
from areal_rsi.infra.execution import JobExecutor
from areal_rsi.infra.trajectories import LocalTrajectoryStore
from examples.tir.benchmark import Example, identity, write_examples
from examples.tir.numeric_audit import NumericAudit, audit, boxed_value


def test_numeric_parser_final_answer_units_and_rejected_expressions() -> None:
    assert boxed_value(r"earlier \boxed{7}, final \boxed{9.00}") == Fraction(9)
    assert boxed_value(r"\boxed{\frac{18}{2}}") == Fraction(9)
    assert boxed_value(r"\boxed{1,234 \text{dollars}}") == Fraction(1234)
    assert boxed_value(r"\boxed{-9.5}") == Fraction(-19, 2)
    for output in (
        r"\boxed{2+7}",
        r"\boxed{\frac{9}{0}}",
        r"\boxed{9} then \boxed{",
        "unboxed 9",
    ):
        assert boxed_value(output) is None


def test_numeric_audit_preserves_raw_evidence_and_checks_task_coverage(tmp_path: Path) -> None:
    import asyncio
    import json

    item = Example(identity("Compute nine"), "Compute nine", "9", "gsm8k-train")
    data = tmp_path / "data.jsonl"
    write_examples(data, (item,))
    jobs = JobExecutor(tmp_path / "execution")
    evaluator = EvaluationService(jobs, LocalTrajectoryStore(tmp_path / "traces"))
    path = jobs.directory("fixture", "eval") / "samples.jsonl"
    sample = EvaluationSample(
        item.task_id, r"\boxed{9.00}", 0, False, (TraceEvent("generation", r"\boxed{9.00}"),)
    )
    payload = json.dumps(encode_record(sample)) + "\n"
    code = f"from pathlib import Path; Path({str(path)!r}).write_text({payload!r})"
    raw = asyncio.run(
        evaluator.evaluate(
            "fixture",
            "eval",
            command(code, tmp_path),
            task_ids=(item.task_id,),
            solution_id="fixture",
        )
    )
    result_path = tmp_path / "raw.json"
    write_record(result_path, raw)
    before = file_ref(path)
    result = audit(result_path, data, tmp_path / "audit")
    assert result.primary_accuracy == 0 and result.numeric_accuracy == 1
    assert result.format_only_errors == 1 and file_ref(path) == before
    assert read_record(tmp_path / "audit/audit.json", NumericAudit) == result
    extra = Example(identity("Another question"), "Another question", "9", "gsm8k-train")
    write_examples(tmp_path / "other.jsonl", (item, extra))
    with pytest.raises(ValueError, match="coverage"):
        audit(result_path, tmp_path / "other.jsonl", tmp_path / "mismatched")
