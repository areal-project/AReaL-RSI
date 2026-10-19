"""Research protocol failures and synthesis behavior, without GPU/model simulations."""

from __future__ import annotations

import json
import random
import re
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from typing import cast

import pytest
from test_training import git_repository

from areal_rsi.api.execution import AReaLRuntime
from areal_rsi.api.records import (
    BenchmarkSplit,
)
from areal_rsi.infra.artifacts import local_path, read_record, write_record
from areal_rsi.infra.coordination import ProblemRun
from examples.tir.benchmark import (
    Benchmark,
    Example,
    ToRLInfo,
    ToRLMessage,
    ToRLReward,
    ToRLRow,
    evaluation_cases,
    identity,
    read_examples,
    torl_examples,
    write_examples,
)
from examples.tir.pipeline import TIRConfig
from examples.tir.problem import ExperimentConfig, TIRProblem
from examples.tir.synthesis import (
    DataRecipe,
    Family,
    FamilyWeight,
    SynthesisPolicy,
    build_dataset,
    generate,
    normalize_gold,
    select_mixed_rollout_curriculum,
)


@pytest.fixture
def experiment(tmp_path: Path) -> ProblemRun:
    from areal_rsi.api.research import ProblemBinding
    from areal_rsi.infra.coordination import ProblemRun, pin_problem

    backend = tmp_path / "backend"
    (backend / "areal").mkdir(parents=True)
    (backend / "areal/__init__.py").touch()
    (backend / "examples/tir").mkdir(parents=True)
    (backend / "examples/tir/tir_math_config.yaml").write_text("test: true\n")
    git_repository(backend)
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text("{}")
    train = write_examples(tmp_path / "train.jsonl", (example("train"),))
    valid = write_examples(tmp_path / "valid.jsonl", (example("valid"),))
    test = write_examples(
        tmp_path / "test.jsonl", (replace(example("test"), source="torl-test", repetitions=4),)
    )
    benchmark = Benchmark(
        17, train, test, train, valid, test, (identity("valid"), identity("test"))
    )
    write_record(tmp_path / "benchmark.json", benchmark)
    config = ExperimentConfig(
        TIRConfig(AReaLRuntime(str(backend), "/unused/python"), str(model), reward="math_verify"),
        str(tmp_path / "benchmark.json"),
    )
    config_ref = write_record(tmp_path / "config.json", config)
    root = tmp_path / "experiment"
    run = ProblemRun(
        "tir",
        root,
        pin_problem(ProblemBinding("examples.tir.problem:TIRProblem", config_ref), root),
    )
    run.prepare()
    return run


def test_tir_protocol_pins_reward_data_and_source(experiment: ProblemRun) -> None:
    problem = cast(TIRProblem, experiment.problem)
    config = replace(problem.config, tir=replace(problem.config.tir, reward="integer"))
    with pytest.raises(ValueError, match="native mathematical equivalence"):
        TIRProblem(config).prepare(experiment.root / "invalid")
    benchmark = problem.benchmark()
    path = local_path(benchmark.valid)
    path.write_text(path.read_text() + "\n")
    with pytest.raises(ValueError, match="changed"):
        _ = experiment.spec


def test_tir_evaluation_preserves_repetitions_and_fixed_harness(experiment: ProblemRun) -> None:
    from areal_rsi.api.research import EvaluationContext

    problem = cast(TIRProblem, experiment.problem)
    context = EvaluationContext(
        experiment.run_id,
        str(experiment.root),
        experiment.spec,
        experiment.spec.baseline,
        BenchmarkSplit.TEST,
        str(experiment.root / "test-output"),
    )
    plan = problem.build_evaluation(context)
    assert len(plan.task_ids) == 4 and plan.clusters and len(set(plan.clusters.values())) == 1
    assert plan.job.slurm and plan.job.slurm.gpus == 2
    assert "srun" not in plan.job.command  # The common executor owns the allocation.
    request_path = Path(plan.job.command[-1])
    from examples.tir.pipeline import WorkerRequest

    request = read_record(request_path, WorkerRequest)
    assert request.config.evaluation == problem.config.tir.evaluation
    assert request.dataset == str(local_path(problem.benchmark().test))


def test_each_candidate_loads_the_current_synthesis_source(
    experiment: ProblemRun, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from areal_rsi.api.research import CandidateContext
    from examples.tir.pipeline import PROJECT
    from examples.tir.problem import RoundRecipe

    project = tmp_path / "editable"
    package = project / "examples/tir"
    package.mkdir(parents=True)
    (project / "examples/__init__.py").touch()
    (package / "__init__.py").touch()
    (package / "benchmark.py").write_bytes((PROJECT / "examples/tir/benchmark.py").read_bytes())
    source = (PROJECT / "examples/tir/synthesis.py").read_text()
    source += '\n    (args.output.parent / "loaded-policy").write_text("version1")\n'
    policy = package / "synthesis.py"
    policy.write_text(source)
    monkeypatch.setattr("examples.tir.problem.PROJECT", project)
    problem = cast(TIRProblem, experiment.problem)
    spec = experiment.spec
    recipe = RoundRecipe(
        "Check fresh policy",
        data=DataRecipe(real_count=1, synthetic_count=0),
        training_steps=1,
        batch_size=1,
        n_samples=2,
    )
    for candidate_id, version in (("r1", "version1"), ("r2", "version2")):
        policy.write_text(source.replace("version1", version))
        directory = experiment.path(candidate_id).parent
        directory.mkdir(parents=True)
        context = CandidateContext(
            experiment.run_id,
            str(experiment.root),
            candidate_id,
            spec.baseline,
            spec,
            str(directory / "job"),
        )
        plan = problem.build_candidate(context, recipe)
        assert plan.job is not None
        assert (directory / "loaded-policy").read_text() == version


def example(prompt: str) -> Example:
    return Example(identity(prompt), prompt, "4", "torl-train")


def test_dataset_preserves_unicode_separators_inside_json_strings(tmp_path: Path) -> None:
    item = example("Two cats\u2028and two dogs\x85How many animals?")
    path = tmp_path / "data.jsonl"
    write_examples(path, (item,))
    assert read_examples(path) == (item,)


def test_word_problem_mixture_reproducible_verified_and_train_only(tmp_path: Path) -> None:
    pool = tuple(example(f"Training question {i}") for i in range(6))
    ref = write_examples(tmp_path / "pool.jsonl", pool)
    recipe = DataRecipe(
        real_count=2,
        synthetic_count=80,
        synthesis=SynthesisPolicy((FamilyWeight("ratio"),), 2, 4),
    )
    first = build_dataset(recipe, pool, ref, (), tmp_path / "first")
    second = build_dataset(recipe, pool, ref, (), tmp_path / "second")
    assert first.dataset.digest == second.dataset.digest
    items = read_examples(tmp_path / "first/train.jsonl")
    generated = [item for item in items if item.source == "verified-synthetic"]
    assert len(items) == 82 and len(generated) == 80
    assert all(item.family == "ratio" and 2 <= item.difficulty <= 4 for item in generated)
    with pytest.raises(ValueError, match="Held-out"):
        build_dataset(recipe, pool, ref, (pool[0].task_id,), tmp_path / "leaked")
    with pytest.raises(ValueError, match="Only official training"):
        build_dataset(recipe, (replace(pool[0], source="torl-test"),), ref, (), tmp_path / "test")
    with pytest.raises(ValueError, match="eligible"):
        build_dataset(replace(recipe, real_count=7), pool, ref, (), tmp_path / "excess")


def test_real_task_id_curriculum_is_exact_and_rejects_invalid_ids(tmp_path: Path) -> None:
    pool = tuple(example(f"Focused training question {i}") for i in range(4))
    ref = write_examples(tmp_path / "focused-pool.jsonl", pool)
    selected = (pool[1].task_id, pool[3].task_id)
    recipe = DataRecipe(real_count=2, synthetic_count=0, real_task_ids=selected)
    build_dataset(recipe, pool, ref, (), tmp_path / "focused")
    result = read_examples(tmp_path / "focused/train.jsonl")
    assert {item.task_id for item in result} == set(selected)
    with pytest.raises(ValueError, match="unique"):
        DataRecipe(real_count=2, synthetic_count=0, real_task_ids=(selected[0], selected[0]))
    unknown = replace(recipe, real_task_ids=(selected[0], "missing-task-id"))
    with pytest.raises(ValueError, match="absent"):
        build_dataset(unknown, pool, ref, (), tmp_path / "unknown")
    heldout = replace(recipe, real_task_ids=(selected[0], pool[0].task_id))
    with pytest.raises(ValueError, match="Held-out"):
        build_dataset(heldout, pool, ref, (pool[0].task_id,), tmp_path / "heldout")


def _rollout(path: Path, prompt: str, rewards: tuple[int, ...], indices: tuple[int, ...]) -> Path:
    rows = [
        {"prompt": f"System\nUser:\n{prompt}\nAssistant:", "sample_idx": index, "reward": reward}
        for index, reward in zip(indices, rewards, strict=True)
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + ("\n" if rows else ""))
    return path


def test_rollout_curriculum_uses_within_file_mixed_rewards(tmp_path: Path) -> None:
    pool = tuple(example(f"Curriculum question {i}") for i in range(3))
    pool = tuple(replace(item, source="torl-train") for item in pool)
    train_ref = write_examples(tmp_path / "official.jsonl", pool)
    trained_ref = write_examples(tmp_path / "trained.jsonl", pool)
    all_zero = _rollout(tmp_path / "zero.jsonl", pool[0].prompt, (0, 0), (0, 1))
    all_one = _rollout(tmp_path / "one.jsonl", pool[0].prompt, (1, 1), (0, 1))
    partial = _rollout(tmp_path / "partial.jsonl", pool[1].prompt, (0, 1), (0, 1))
    complete = _rollout(tmp_path / "complete.jsonl", pool[2].prompt, (0, 1, 0, 1), (0, 1, 2, 3))
    selection = select_mixed_rollout_curriculum(
        pool,
        pool,
        (all_zero, all_one, partial, complete),
        frozen_pool_source=train_ref,
        trained_dataset_source=trained_ref,
        n_samples=4,
    )
    assert selection.selected_task_ids == (pool[1].task_id, pool[2].task_id)
    assert selection.complete_mixed_group_count == 1
    assert selection.partial_mixed_group_count == 1
    duplicate = _rollout(tmp_path / "duplicate.jsonl", pool[2].prompt, (0, 1), (0, 0))
    with pytest.raises(ValueError, match="duplicate"):
        select_mixed_rollout_curriculum(
            pool,
            pool,
            (duplicate,),
            frozen_pool_source=train_ref,
            trained_dataset_source=trained_ref,
            n_samples=4,
        )
    unknown = _rollout(tmp_path / "unknown.jsonl", "Unknown curriculum question", (0, 1), (0, 1))
    with pytest.raises(ValueError, match="absent"):
        select_mixed_rollout_curriculum(
            pool,
            pool,
            (unknown,),
            frozen_pool_source=train_ref,
            trained_dataset_source=trained_ref,
            n_samples=4,
        )
    with pytest.raises(ValueError, match="absent"):
        select_mixed_rollout_curriculum(
            pool,
            pool,
            (partial,),
            frozen_pool_source=train_ref,
            trained_dataset_source=trained_ref,
            n_samples=4,
            excluded_ids=(pool[1].task_id,),
        )


def test_rollout_curriculum_validates_mixed_trained_data_and_skips_synthetic(
    tmp_path: Path,
) -> None:
    official = tuple(
        replace(example(f"Official curriculum question {i}"), source="torl-train") for i in range(2)
    )
    synthetic = replace(example("Synthetic curriculum question"), source="verified-synthetic")
    trained = (*official, synthetic)
    pool_ref = write_examples(tmp_path / "official.jsonl", official)
    trained_ref = write_examples(tmp_path / "trained.jsonl", trained)
    mixed_official = _rollout(tmp_path / "official-mixed.jsonl", official[0].prompt, (0, 1), (0, 1))
    synthetic_mixed = _rollout(tmp_path / "synthetic-mixed.jsonl", synthetic.prompt, (0, 1), (0, 1))
    selection = select_mixed_rollout_curriculum(
        official,
        trained,
        (mixed_official, synthetic_mixed),
        frozen_pool_source=pool_ref,
        trained_dataset_source=trained_ref,
        n_samples=2,
    )
    assert selection.selected_task_ids == (official[0].task_id,)
    assert selection.complete_mixed_group_count == 1


@pytest.mark.parametrize(
    "bad_row",
    [
        {"sample_idx": True, "reward": 0.0},
        {"sample_idx": -1, "reward": 0.0},
        {"sample_idx": 2, "reward": 0.0},
        {"sample_idx": 0, "reward": 0.5},
        {"sample_idx": 0, "reward": float("nan")},
    ],
)
def test_rollout_curriculum_rejects_invalid_typed_logger_fields(
    tmp_path: Path, bad_row: dict[str, object]
) -> None:
    pool = (replace(example("Typed curriculum question"), source="torl-train"),)
    pool_ref = write_examples(tmp_path / "official.jsonl", pool)
    trained_ref = write_examples(tmp_path / "trained.jsonl", pool)
    row = {
        "task_id": 1,
        "prompt": f"System\nUser:\n{pool[0].prompt}\nAssistant:",
        **bad_row,
    }
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps(row, allow_nan=True) + "\n")
    with pytest.raises(ValueError, match="invalid"):
        select_mixed_rollout_curriculum(
            pool,
            pool,
            (path,),
            frozen_pool_source=pool_ref,
            trained_dataset_source=trained_ref,
            n_samples=2,
        )


@pytest.mark.parametrize(
    ("family", "required_terms"),
    [
        ("percent", ("costs $", "%", "discounts")),
        ("average", ("average", "scores")),
        ("units", ("cups", "container")),
        ("work", ("earns $", "hours", "supplies")),
        ("decimal", ("earns $", "per package", "tip")),
    ],
)
def test_new_synthetic_families_have_semantic_prompts_and_exact_targets(
    family: Family, required_terms: tuple[str, ...]
) -> None:
    for seed in range(40):
        item = generate(random.Random(seed), family, 5)
        prompt = item.prompt.casefold()
        assert all(term in prompt for term in required_terms)
        assert item.task_id == identity(item.prompt)
        assert item.answer.isdigit() and int(item.answer) >= 0
        if family == "average":
            values = [int(value) for value in re.findall(r"\d+", item.prompt.split(":", 1)[1])]
            assert len(values) >= 3 and sum(values) % len(values) == 0
            assert sum(values) // len(values) == int(item.answer)
        elif family == "units":
            numbers = [int(value) for value in re.findall(r"\d+", item.prompt)]
            assert numbers[0] * numbers[1] % numbers[2] == 0
            assert Fraction(numbers[0] * numbers[1], numbers[2]) == int(item.answer)
        elif family == "percent":
            match = re.search(r"costs \$(\d+).*?by (\d+)%.*?by (\d+)%", item.prompt)
            assert match is not None
            price, increase, discount = map(int, match.groups())
            expected = (
                Fraction(price) * Fraction(100 + increase, 100) * Fraction(100 - discount, 100)
            )
            assert expected.denominator == 1
            assert int(item.answer) == expected.numerator
        elif family == "work":
            match = re.search(
                r"earns \$(\d+) per hour, works (\d+) hours each day for (\d+) days, .*?"
                r"and spends \$(\d+)",
                item.prompt,
            )
            assert match is not None
            hourly, hours, days, expense = map(int, match.groups())
            assert int(item.answer) == hourly * hours * days - expense
        elif family == "decimal":
            rate_match = re.search(r"\$(\d+\.\d+)", item.prompt)
            quantity_match = re.search(r"delivers (\d+)", item.prompt)
            bonus_match = re.search(r"\$(\d+) tip", item.prompt)
            assert rate_match is not None
            assert quantity_match is not None
            assert bonus_match is not None
            rate = Fraction(rate_match.group(1))
            quantity = int(quantity_match.group(1))
            bonus = int(bonus_match.group(1))
            assert rate * quantity + bonus == int(item.answer)


@pytest.mark.parametrize("family", ["fraction", "radical", "linear", "counting", "sequence"])
def test_extended_synthetic_families_are_reproducible_and_exact(family: Family) -> None:
    first = [
        generate(random.Random(seed), family, difficulty)
        for difficulty in range(1, 6)
        for seed in range(20)
    ]
    second = [
        generate(random.Random(seed), family, difficulty)
        for difficulty in range(1, 6)
        for seed in range(20)
    ]
    assert first == second
    for item in first:
        if family == "fraction":
            match = re.search(r"needs (\d+) and (\d+)/(\d+) cups", item.prompt)
            assert match is not None
            whole, numerator, denominator = map(int, match.groups())
            assert Fraction(item.answer) == Fraction(whole) + Fraction(numerator, denominator)
        elif family == "radical":
            sides = re.search(r"lengths (\d+) and (\d+)", item.prompt)
            assert sides is not None
            left, right = map(int, sides.groups())
            answer = item.answer
            simple = re.fullmatch(r"(\d+)", answer)
            factored = re.fullmatch(r"(?:(\d+)\\sqrt\{(\d+)\}|\\sqrt\{(\d+)\})", answer)
            if simple:
                rendered_square = int(simple.group(1)) ** 2
            else:
                assert factored is not None
                rendered_square = int(factored.group(1) or 1) ** 2 * int(
                    factored.group(2) or factored.group(3)
                )
            assert rendered_square == left * left + right * right
        elif family == "linear":
            match = re.search(r"(\d+)x \+ (\d+) = (\d+)", item.prompt)
            assert match is not None
            coefficient, offset, total = map(int, match.groups())
            assert coefficient * int(item.answer) + offset == total
        elif family == "counting":
            match = re.search(
                r"of (\d+) different people is chosen from (\d+) volunteers", item.prompt
            )
            assert match is not None
            choose, people = map(int, match.groups())
            selected = 1
            for value in range(people - choose + 1, people + 1):
                selected *= value
            for value in range(2, choose + 1):
                selected //= value
            assert int(item.answer) == selected
        else:
            match = re.search(
                r"starts with (\d+) and has common difference (\d+).*?(\d+)th term",
                item.prompt,
            )
            assert match is not None
            first_term, difference, term = map(int, match.groups())
            assert int(item.answer) == first_term + (term - 1) * difference
        assert item.task_id == identity(item.prompt)


def test_training_gold_normalization_only_delimits_math_answers() -> None:
    assert normalize_gold(r"\sqrt{2}", "math_delimited") == r"$\sqrt{2}$"
    assert normalize_gold(r"\frac{1}{2}", "math_delimited") == r"$\frac{1}{2}$"
    assert normalize_gold("42", "math_delimited") == "42"
    assert normalize_gold(r"$\sqrt{2}$", "math_delimited") == r"$\sqrt{2}$"
    assert normalize_gold(r"\sqrt{2}", "preserve") == r"\sqrt{2}"


def test_native_torl_preserves_repeated_evaluations_and_symbolic_answers() -> None:
    row = ToRLRow(
        "math500",
        (ToRLMessage("native instructions", "system"), ToRLMessage("Find x", "user")),
        "MATH",
        ToRLReward(r"\frac{1}{2}", "rule"),
        ToRLInfo("original-id", "test"),
    )
    items = torl_examples((row,) * 4, training=False)
    assert len(items) == 1 and items[0].repetitions == 4
    cases = evaluation_cases(items)
    assert len({case.task_id for case in cases}) == 4
    assert {case.question_id for case in cases} == {identity("Find x")}
    assert all(case.answer == r"\frac{1}{2}" for case in cases)
    training = torl_examples((row,) * 4, training=True)
    assert len(training) == 1 and training[0].repetitions == 1
    with pytest.raises(ValueError, match="conflicting"):
        torl_examples((row, replace(row, reward_model=ToRLReward("2", "rule"))), training=False)


def test_native_training_mixture_keeps_fractional_gold_and_excludes_test(tmp_path: Path) -> None:
    item = replace(example("A native training problem"), source="torl-train", answer=r"\frac{1}{2}")
    pool = (item,)
    ref = write_examples(tmp_path / "pool.jsonl", pool)
    recipe = DataRecipe(real_count=1, synthetic_count=0)
    build_dataset(recipe, pool, ref, (), tmp_path / "native")
    assert read_examples(tmp_path / "native/train.jsonl") == pool
    with pytest.raises(ValueError, match="Only official training"):
        build_dataset(recipe, (replace(item, source="torl-test"),), ref, (), tmp_path / "leaked")
