from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from areal_rsi_tir.assets import (
    checkpoint_digest,
    source_files_digest,
    tir_verifier_files,
)
from areal_rsi_tir.contracts import TirTraceBundle, TirTrajectory
from areal_rsi_tir.cycle import TirCycle
from areal_rsi_tir.evaluation import (
    complete_tir_report,
    tir_trajectory,
    verified_tir_case,
)
from areal_rsi_tir.harness import load_tir_harness
from areal_rsi_tir.tasks import load_tir_task_set
from areal_rsi_tir.verifier import strict_math_reward

from areal_rsi.api.control import json_bytes, read_record
from areal_rsi.api.records import RsiCaseScore, RsiSplit
from areal_rsi.api.research import GenerationSpec, HarnessSelectionRule, RsiPromotionRule
from areal_rsi.scripts.jobs import AReaLJobRunner, digest_bytes


def test_clock_answer_is_not_division_by_zero() -> None:
    pytest.importorskip("math_verify")
    gold = r"\boxed{11:00}"
    assert strict_math_reward(gold, gold) == 1.0
    assert strict_math_reward(r"They meet at \boxed{11:00}.", gold) == 1.0
    assert strict_math_reward(r"\boxed{\text{11:00}}", gold) == 1.0
    assert strict_math_reward(r"\boxed{12:00}", gold) == 0.0
    assert strict_math_reward(r"\boxed{\infty}", gold) == 0.0
    assert strict_math_reward(r"\boxed{3:2}", r"\boxed{1.5}") == 1.0
    assert strict_math_reward(r"\boxed{\frac{19}{16}}", r"\boxed{19:16}") == 1.0


def test_large_integer_comparison_has_exact_inequality_fallback() -> None:
    pytest.importorskip("math_verify")
    huge = r"\boxed{2^{100000000}}"
    assert strict_math_reward(huge, r"\boxed{3}") == 0.0
    assert strict_math_reward(huge, huge) == 1.0
    # The exponent remains a Pow in the parser rather than an Integer.
    nested = r"\boxed{2^{2^{1981}}}"
    assert strict_math_reward(nested, r"\boxed{2^{1984}-3}") == 0.0
    assert strict_math_reward(r"\boxed{2^{2^{1981}}+4}", r"\boxed{2^{1984}-3}") == 0.0
    assert strict_math_reward(nested, nested) == 1.0
    tower = r"\boxed{2004 + 2003^{2002^{2001}}}"
    assert strict_math_reward(tower, r"\boxed{4003}") == 0.0
    assert strict_math_reward(tower, tower) == 1.0


def test_nested_factorial_comparison_does_not_expand_huge_integer() -> None:
    pytest.importorskip("math_verify")
    assert strict_math_reward(r"\boxed{11!!}", r"\boxed{2}") == 0.0
    assert strict_math_reward(r"\boxed{3!}", r"\boxed{6}") == 1.0
    sympy = pytest.importorskip("sympy")
    from areal_rsi_tir.verifier import _integer_bound_counterexample

    for argument, expected in [(0, 1), (1, 1), (3, 6)]:
        value = sympy.factorial(argument, evaluate=False)
        assert not _integer_bound_counterexample(sympy.Integer(expected), value)
    assert not _integer_bound_counterexample(
        sympy.Integer(2), sympy.factorial(sympy.Symbol("n"), evaluate=False)
    )


def test_integer_bounds_do_not_confuse_zero_or_negative_powers() -> None:
    sympy = pytest.importorskip("sympy")
    from areal_rsi_tir.verifier import _integer_bound_counterexample

    tower = sympy.Pow(2003, sympy.Pow(2002, 2001, evaluate=False), evaluate=False)
    assert _integer_bound_counterexample(sympy.Integer(4003), tower)
    # A huge exponent alone does not imply a large value.
    for base in (0, 1):
        assert not _integer_bound_counterexample(
            sympy.Integer(base), sympy.Pow(base, tower, evaluate=False)
        )
    assert not _integer_bound_counterexample(sympy.Integer(0), sympy.Mul(0, tower, evaluate=False))
    assert not _integer_bound_counterexample(sympy.Integer(1), sympy.Pow(2, -1, evaluate=False))
    assert not _integer_bound_counterexample(sympy.Integer(1), sympy.Add(tower, -1, evaluate=False))


def test_symbolic_equation_chain_has_exact_counterexample_fallback() -> None:
    pytest.importorskip("math_verify")
    response = r"\(\frac{1}{\binom{n}{2009}} = \frac{2009!(n-2009)!}{n!}\)"
    assert strict_math_reward(response, r"\boxed{\frac{2009}{2008}}") == 0.0
    assert strict_math_reward(r"\(x=\frac{1}{2}\)", r"\boxed{0.5}") == 1.0
    assert strict_math_reward(r"\boxed{\frac{n!}{n!}}", r"\boxed{1}") == 1.0


def test_comparison_retry_preserves_equality_and_propagates_failure(monkeypatch) -> None:
    grader = pytest.importorskip("math_verify.grader")
    errors = pytest.importorskip("math_verify.errors")
    original = grader.verify
    attempts = 0

    def once_timed_out(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise errors.TimeoutException("comparison timeout")
        return original(*args, **kwargs)

    monkeypatch.setattr(grader, "verify", once_timed_out)
    assert strict_math_reward(r"\boxed{\frac{1}{2}}", r"\boxed{0.5}") == 1.0
    # Both certificate primes divide the difference; collisions cannot prove equality.
    attempts = 0
    assert strict_math_reward(r"\boxed{10406}", r"\boxed{3}") == 0.0

    def always_timed_out(*args, **kwargs):
        raise errors.TimeoutException("persistent comparison timeout")

    monkeypatch.setattr(grader, "verify", always_timed_out)
    with pytest.raises(errors.TimeoutException, match="persistent comparison timeout"):
        strict_math_reward(r"\boxed{\frac{1}{2}}", r"\boxed{0.5}")
    # Equal values at every sampled point cannot certify symbolic equality.
    with pytest.raises(errors.TimeoutException, match="persistent comparison timeout"):
        strict_math_reward(r"\boxed{\frac{n!}{n!}}", r"\boxed{1}")


def test_training_reward_contains_persistent_math_timeout(monkeypatch) -> None:
    errors = pytest.importorskip("math_verify.errors")
    from areal_rsi_tir import verifier

    def timed_out(response: str, answer: str) -> float:
        raise errors.TimeoutException("persistent comparison timeout")

    monkeypatch.setattr(verifier, "strict_math_reward", timed_out)
    assert verifier.strict_tir_reward_fn("question", r"\boxed{2}", [1], [2], r"\boxed{2}") == 0.0


def test_tir_verification_excludes_tool_observation_tokens() -> None:
    trajectory = {
        "input_ids": [[1, 2, 3, 4]],
        "loss_mask": [[0, 1, 0, 1]],
        "attention_mask": [[True, True, True, True]],
        "rewards": [1.0],
    }

    def verify(response: str, answer: str) -> float:
        assert answer == "24"
        return float(response == answer)

    case = verified_tir_case(
        "case1",
        trajectory,
        answer="24",
        decode=lambda ids: "".join(str(token) for token in ids),
        verify=verify,
    )
    assert case.response == "24"
    assert case.generated_tokens == 2
    assert case.score == 1.0
    evidence = tir_trajectory("case1", trajectory)
    assert evidence.input_ids == (1, 2, 3, 4)
    assert evidence.loss_mask == (0, 1, 0, 1)
    assert evidence.attention_mask == (1, 1, 1, 1)
    trajectory["rewards"] = [0.0]
    with pytest.raises(ValueError, match="disagrees"):
        verified_tir_case(
            "case1",
            trajectory,
            answer="24",
            decode=lambda ids: "".join(str(token) for token in ids),
            verify=verify,
        )


def test_trace_bundle_preserves_binary_masks_without_gold_answers(tmp_path: Path) -> None:
    localized = {
        "input_ids": [[1, 2, 3]],
        "loss_mask": [[0, 1, 0]],
        "attention_mask": [[True, True, True]],
        "rewards": [0.0],
    }
    trace = tir_trajectory("case1", localized)
    bundle = TirTraceBundle(
        1,
        "taskset_t0",
        RsiSplit.HARNESS_EVOLVE,
        "sha256:" + "1" * 64,
        "harness_h0",
        "sha256:" + "2" * 64,
        "sha256:" + "3" * 64,
        (trace,),
    )
    encoded = json_bytes(bundle)
    assert b"answer" not in encoded
    assert json.loads(encoded)["trajectories"][0]["attention_mask"] == [1, 1, 1]
    path = tmp_path / "trace.json"
    path.write_bytes(encoded)
    assert read_record(path, TirTraceBundle) == bundle
    assert digest_bytes(encoded).startswith("sha256:")
    path.write_bytes(json_bytes(replace(bundle, trajectories=(replace(trace, reward=1.0),))))
    assert read_record(path, TirTraceBundle) != bundle
    assert digest_bytes(path.read_bytes()) != digest_bytes(encoded)


@pytest.mark.parametrize("tampered", [False, True])
def test_controller_rejects_a_report_with_changed_trace_bundle(
    frozen_inputs: tuple[Path, Path, Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tampered: bool,
) -> None:
    task_root, h0_root, runner, model = frozen_inputs
    task_set = load_tir_task_set(task_root)
    harness, _ = load_tir_harness(h0_root, runner)
    model_digest = checkpoint_digest(model)
    verifier_digest = source_files_digest(tir_verifier_files(runner))
    candidate = tmp_path / "candidate.txt"
    candidate.write_text("Candidate {prompt}")
    spec = GenerationSpec(
        "run1",
        str(task_root),
        str(h0_root),
        str(candidate),
        str(runner),
        str(model),
        "/usr/bin/python3",
        str(runner / "config.yaml"),
        "/shared/experiments",
        "/shared/name_resolve",
        "demo",
        1,
        2,
        1,
        HarnessSelectionRule(0, 1),
        RsiPromotionRule("joint_performance", 0, 1, 1),
    )
    jobs = AReaLJobRunner(tmp_path / "run/jobs", "run1")
    task_id = task_set.harness_evolve.task_ids[0]

    def fake_run(stage: str, command: tuple[str, ...], output: Path) -> Path:
        del stage, command
        bundle_path = output.with_suffix(".trajectories.json")
        trace = TirTrajectory(task_id, (1, 2), (0, 1), (1, 1), 0.0)
        bundle = TirTraceBundle(
            1,
            task_set.task_set_id,
            RsiSplit.HARNESS_EVOLVE,
            model_digest,
            harness.harness_id,
            harness.runner_digest,
            verifier_digest,
            (trace,),
        )
        encoded = json_bytes(bundle)
        bundle_path.parent.mkdir(parents=True, exist_ok=True)
        bundle_path.write_bytes(encoded)
        report = complete_tir_report(
            task_set,
            RsiSplit.HARNESS_EVOLVE,
            (RsiCaseScore(task_id, 0.0, "wrong", 1),),
            model_digest=model_digest,
            harness_id=harness.harness_id,
            runner_digest=harness.runner_digest,
            verifier_digest=verifier_digest,
            seed=1,
            temperature=1.0,
            max_new_tokens=32,
            trajectory_path=str(bundle_path),
            trajectory_digest=digest_bytes(encoded),
        )
        output.write_bytes(json_bytes(report))
        if tampered:
            bundle_path.write_bytes(
                json_bytes(replace(bundle, trajectories=(replace(trace, reward=1.0),)))
            )
        return output

    monkeypatch.setattr(jobs, "run", fake_run)

    def action() -> object:
        return TirCycle(tmp_path / "data")._evaluate(
            jobs,
            spec,
            task_set,
            harness,
            h0_root,
            model,
            model_digest,
            RsiSplit.HARNESS_EVOLVE,
            "baseline",
            1,
            verifier_digest,
        )

    if tampered:
        with pytest.raises(ValueError, match="traces changed"):
            action()
    else:
        assert action().cases[0].score == 0.0
