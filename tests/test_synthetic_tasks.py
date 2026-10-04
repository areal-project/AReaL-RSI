"""Synthetic training must preserve the original evaluation boundaries."""

from __future__ import annotations

import json
import re
from fractions import Fraction
from pathlib import Path

import pytest
from areal_rsi_tir.synthesis import (
    freeze_tir_synthetic_probe,
    freeze_tir_synthetic_training,
    select_model_calibrated_cases,
    synthesize_tir_math_cases,
)
from areal_rsi_tir.synthetic_experiment import retained_state
from areal_rsi_tir.tasks import (
    TirSplitSizes,
    freeze_tir_task_set,
    load_tir_task_set,
)

from areal_rsi.api.records import RsiSplit


def test_synthesis_rejects_a_nonterminal_weight_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    decision = tmp_path / "decision.json"
    decision.write_text(json.dumps({"generation": 1, "decision": "continue_to_generation_2"}))
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            {
                "terminal_generation": 1,
                "parent_generation": 1,
                "decision_path": str(decision),
                "model_root": str(tmp_path / "model"),
                "harness_root": str(tmp_path / "harness"),
                "task_set_root": str(tmp_path / "tasks"),
            }
        )
    )
    monkeypatch.setenv("RSI_RETAINED_STATE_PATH", str(state))
    with pytest.raises(ValueError, match="terminal weight loop"):
        retained_state()


def test_synthetic_math_is_deterministic_balanced_and_distinct() -> None:
    first = synthesize_tir_math_cases(seed=41, count=512)
    assert first == synthesize_tir_math_cases(seed=41, count=512)
    assert first != synthesize_tir_math_cases(seed=42, count=512)
    assert len({item.case.task_id for item in first}) == 512
    assert {item.family.value for item in first} == {
        "tickets",
        "ratio",
        "discount",
        "rectangle",
        "sequence",
        "machines",
        "remaining_fraction",
        "linear",
    }
    assert all(80 <= len(item.case.question) <= 250 for item in first)
    assert all(item.case.answer and "\\boxed" not in item.case.question for item in first)
    with pytest.raises(ValueError, match="divisible by eight"):
        synthesize_tir_math_cases(seed=41, count=10)


def test_model_calibration_excludes_trivial_cases_and_requires_borderline_cases() -> None:
    candidates = synthesize_tir_math_cases(seed=41, count=32)
    scores = tuple(1 if index < 8 else 0 if index < 16 else 2 for index in range(32))
    selected = select_model_calibrated_cases(candidates, scores, count=8, seed=41)
    assert selected.one_pass == 8
    assert selected.zero_passes == 0
    assert selected.eligible_cases == 16
    assert {case.family for case in selected.cases} == {case.family for case in candidates}
    incomplete = (None,) * 8 + scores[8:]
    with pytest.raises(ValueError, match="Too few borderline"):
        select_model_calibrated_cases(candidates, incomplete, count=8, seed=41)
    completed_only = select_model_calibrated_cases(
        candidates, scores[:8] + (None,) * 8 + scores[16:], count=8, seed=41
    )
    assert completed_only.eligible_cases == 8
    assert completed_only.cases == selected.cases
    with pytest.raises(ValueError, match="Too few model-calibrated"):
        select_model_calibrated_cases(candidates, (2,) * 32, count=8, seed=41)


def test_synthetic_training_is_pure_and_keeps_frozen_evaluation_splits(tmp_path: Path) -> None:
    source = tmp_path / "train.jsonl"
    source.write_text(
        "\n".join(
            json.dumps(
                {
                    "messages": [{"role": "user", "content": f"Original task {i}: {i}+7?"}],
                    "answer": str(i + 7),
                }
            )
            for i in range(23)
        )
        + "\n"
    )
    test = tmp_path / "test.jsonl"
    test.write_text(
        json.dumps({"messages": [{"role": "user", "content": "Held-out 9+7?"}], "answer": "16"})
        + "\n"
    )
    parent_root, derived_root = tmp_path / "parent", tmp_path / "derived"
    parent = freeze_tir_task_set(
        source,
        test,
        parent_root,
        sizes=TirSplitSizes(2, 2, 16, 3),
        seed=11,
    )
    probe_root = tmp_path / "probe"
    probe = freeze_tir_synthetic_probe(
        parent_root,
        probe_root,
        seed=41,
        count=32,
        excluded_task_set_roots=(parent_root,),
    )
    assert len(probe.harness_evolve.task_ids) == 32
    assert probe.model_train == probe.harness_evolve == probe.harness_select
    assert load_tir_task_set(probe_root) == probe
    derived = freeze_tir_synthetic_training(
        parent_root,
        derived_root,
        synthesize_tir_math_cases(seed=41, count=16),
        seed=41,
        probe_task_set_id=probe.task_set_id,
        probe_report_digests=("sha256:first", "sha256:second"),
        selected_pass_counts=(8, 8),
        excluded_task_set_roots=(parent_root,),
    )
    assert derived.task_set_id != parent.task_set_id
    assert len(derived.model_train.task_ids) == len(parent.model_train.task_ids) == 16
    assert set(derived.model_train.task_ids).isdisjoint(parent.model_train.task_ids)
    assert derived.model_train == derived.harness_evolve == derived.harness_select
    for split in RsiSplit:
        if split in (RsiSplit.MODEL_TRAIN, RsiSplit.HARNESS_EVOLVE, RsiSplit.HARNESS_SELECT):
            continue
        assert getattr(derived, split.value) == getattr(parent, split.value)
        assert (derived_root / "public" / f"{split.value}.jsonl").read_bytes() == (
            parent_root / "public" / f"{split.value}.jsonl"
        ).read_bytes()
    assert load_tir_task_set(derived_root) == derived
    provenance = json.loads((derived_root / "synthetic-provenance.json").read_bytes())
    assert provenance["synthetic_train_cases"] == 16
    assert provenance["original_train_cases"] == 0
    synthetic_path = derived_root / "private/synthetic-source.jsonl"
    original_synthetic = synthetic_path.read_bytes()
    synthetic_path.write_bytes(original_synthetic + b"\n")
    with pytest.raises(ValueError, match="Synthetic training derivation changed"):
        load_tir_task_set(derived_root)
    synthetic_path.write_bytes(original_synthetic)
    derivation_path = derived_root / "private/train-derivation.json"
    original_derivation = derivation_path.read_bytes()
    derivation_path.unlink()
    with pytest.raises(ValueError, match="Synthetic training derivation is incomplete"):
        load_tir_task_set(derived_root)
    derivation_path.write_bytes(original_derivation)
    train_path = derived_root / "public/model_train.jsonl"
    train_path.chmod(0o600)
    train_path.write_bytes(train_path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="public split changed"):
        load_tir_task_set(derived_root)


def test_fractional_linear_answers_satisfy_unique_equations() -> None:
    for item in synthesize_tir_math_cases(seed=20261002, count=3072):
        if item.family.value != "linear":
            continue
        match = re.fullmatch(
            r"Solve for x: \((\d+)\*x \+ (\d+)\)/(\d+) - "
            r"\((\d+)\*x \+ (\d+)\)/(\d+) = (-?\d+(?:/\d+)?). "
            r"Give the exact value of x as an integer or a reduced fraction\.",
            item.case.question,
        )
        assert match is not None
        a, b, d, c, e, f = map(int, match.groups()[:6])
        rhs = Fraction(match.group(7))
        coefficient = Fraction(a, d) - Fraction(c, f)
        assert coefficient != 0
        expected = (rhs - Fraction(b, d) + Fraction(e, f)) / coefficient
        assert Fraction(item.case.answer) == expected


def test_supplement_answers_follow_ratio_and_production_constraints() -> None:
    from areal_rsi_tir.synthesis import synthesize_tir_supplement

    cases = synthesize_tir_supplement(seed=20261004)
    assert len(cases) == len({item.case.task_id for item in cases}) == 256
    assert cases == synthesize_tir_supplement(seed=20261004)
    for item in cases:
        q = item.case.question
        if item.family.value == "ratio":
            values = list(map(int, re.findall(r"\d+", q)))
            red, blue, added, after_red, after_blue = values
            total = Fraction(item.case.answer)
            initial_blue = total * blue / (red + blue)
            initial_red = total - initial_blue
            assert initial_blue.denominator == initial_red.denominator == 1
            assert (initial_red + added) / initial_blue == Fraction(after_red, after_blue)
            assert Fraction(red, blue) != Fraction(after_red, after_blue)
        else:
            match = re.fullmatch(
                r"(\d+) identical machines produce (\d+) parts in (\d+) hours at a constant rate. "
                r"A new shift starts with (\d+) machines working at that rate for (\d+) hours. "
                r"Then (\d+) machines stop; each remaining machine works at (\d+(?:/\d+)?) "
                r"times its original rate for (\d+) more hours. How many parts are produced "
                r"during the new shift\? Give an exact integer or reduced fraction\.",
                q,
            )
            assert match is not None
            machines, parts, hours, active, first_hours, stopped = map(int, match.groups()[:6])
            rate = Fraction(parts, machines * hours)
            factor, second_hours = Fraction(match.group(7)), int(match.group(8))
            first_stage = sum(rate * first_hours for _ in range(active))
            second_stage = sum(rate * factor * second_hours for _ in range(active - stopped))
            assert Fraction(item.case.answer) == first_stage + second_stage
