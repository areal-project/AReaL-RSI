"""Research protocol failures and synthesis behavior, without GPU/model simulations."""

from __future__ import annotations

import asyncio
import json
import random
import re
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest
from test_training import evaluation, git_repository

from areal_rsi.api.execution import AReaLRuntime, TrainingResult
from areal_rsi.api.records import (
    BenchmarkSplit,
)
from areal_rsi.infra.artifacts import local_path, write_record
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
from examples.tir.experiment import Candidate, Experiment, ExperimentConfig, prepare, protocol
from examples.tir.pipeline import TIRConfig
from examples.tir.synthesis import (
    DataRecipe,
    Family,
    FamilyWeight,
    SynthesisManifest,
    SynthesisPolicy,
    build_dataset,
    generate,
    normalize_gold,
    select_mixed_rollout_curriculum,
)


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


@pytest.fixture
def experiment(tmp_path: Path) -> Experiment:
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
    valid = write_examples(tmp_path / "valid.jsonl", tuple(example(f"valid-{i}") for i in range(4)))
    test = write_examples(
        tmp_path / "test.jsonl", (replace(example("test"), source="torl-test", repetitions=4),)
    )
    manifest = Benchmark(17, train, test, train, valid, test, (identity("valid"), identity("test")))
    write_record(tmp_path / "benchmark.json", manifest)
    config = ExperimentConfig(
        TIRConfig(AReaLRuntime(str(backend), "/unused/python"), str(model), reward="math_verify"),
        str(tmp_path / "benchmark.json"),
    )
    root = tmp_path / "experiment"
    prepare(config, root)
    return Experiment(root)


def test_native_protocol_refuses_integer_only_reward(
    experiment: Experiment, tmp_path: Path
) -> None:
    items = (
        replace(example("Native train"), source="torl-train", answer=r"\frac{1}{2}"),
        replace(example("Native valid"), source="torl-train", answer=r"\frac{1}{2}"),
        replace(example("Native test"), source="torl-test", answer=r"\frac{1}{2}"),
    )
    refs = tuple(
        write_examples(tmp_path / f"native-{index}.jsonl", (item,))
        for index, item in enumerate(items)
    )
    benchmark = replace(
        experiment.protocol.benchmark,
        train=refs[0],
        valid=refs[1],
        test=refs[2],
        excluded_ids=(items[1].task_id, items[2].task_id),
    )
    path = tmp_path / "native-benchmark.json"
    write_record(path, benchmark)
    config = replace(
        experiment.protocol.config,
        tir=replace(experiment.protocol.config.tir, reward="integer"),
        benchmark=str(path),
    )
    with pytest.raises(ValueError, match="native mathematical equivalence"):
        prepare(config, tmp_path / "invalid-native-experiment")


def test_selection_requires_multiple_rounds_best_valid_and_is_immutable(
    experiment: Experiment,
) -> None:
    root, frozen = experiment.root, experiment.protocol
    valid_ids = tuple(item.task_id for item in read_examples(local_path(frozen.benchmark.valid)))
    base = evaluation(
        root / "base-samples.jsonl",
        (True, False, False, False),
        task_ids=valid_ids,
        solution_id="base",
    )
    write_record(root / "valid/base.json", base)
    recipe_ref = write_record(root / "recipe.json", DataRecipe())
    data = SynthesisManifest(DataRecipe(), frozen.benchmark.train, frozen.benchmark.train, 1, 0)

    def candidate(name: str, passed: tuple[bool, ...]) -> None:
        valid = evaluation(
            root / f"{name}-samples.jsonl", passed, task_ids=valid_ids, solution_id=name
        )
        solution = replace(frozen.baseline, solution_id=name)
        # This fixture tests selection only; it does not represent real training.
        training = TrainingResult("fixture", solution.model.content, frozen.base_model, 1, {})
        record = Candidate(
            name,
            recipe_ref,
            data,
            frozen.project,
            frozen.baseline,
            "completed",
            training,
            solution,
            valid,
        )
        write_record(root / f"rounds/{name}/candidate.json", record)

    candidate("r1", (True, True, False, False))
    with pytest.raises(ValueError, match="minimum number"):
        experiment.select("r1")
    candidate("r2", (True, True, True, False))
    original = experiment.candidate("r2")
    assert original.valid is not None
    forged = replace(
        original,
        valid=replace(original.valid, report=replace(original.valid.report, score=1)),
    )
    write_record(root / "rounds/r2/candidate.json", forged)
    with pytest.raises(ValueError, match="sample evidence"):
        experiment.select("r2")
    write_record(root / "rounds/r2/candidate.json", original)
    with pytest.raises(ValueError, match="best valid"):
        experiment.select("r1")
    selected = experiment.select("r2")
    assert selected.valid_comparison.delta == 0.5 and selected.completed_rounds == ("r1", "r2")
    with pytest.raises(ValueError, match="immutable"):
        experiment.select("r1")
    with pytest.raises(ValueError, match="Selection is frozen"):
        asyncio.run(experiment.evaluate("r1"))
    with pytest.raises(ValueError, match="restricted"):
        asyncio.run(experiment.evaluate("r1", test=True))
    # Independent acceptance reuses persisted samples, preserving the official
    # repeated cases as one question rather than four independent observations.
    cases = evaluation_cases(read_examples(local_path(frozen.benchmark.test)))
    task_ids = tuple(case.task_id for case in cases)
    for name, passed in (("base", False), ("r2", True)):
        saved = evaluation(
            root / f"test-{name}-samples.jsonl", (passed,) * 4, task_ids=task_ids, solution_id=name
        )
        saved = replace(saved, report=replace(saved.report, split=BenchmarkSplit.TEST))
        write_record(root / f"test/{name}.json", saved)
    result = asyncio.run(experiment.test("r2"))
    assert result.target_met and result.comparison.delta == 1
    assert result.comparison.count == 4 and result.comparison.independent_questions == 1
    assert (root / "test/result.json").exists()
    assert "Test / official test" in (root / "report.md").read_text()


def test_frozen_protocol_rejects_data_and_source_changes(experiment: Experiment) -> None:
    source = Path(experiment.config.runtime.repository) / "examples/tir/tir_math_config.yaml"
    source.write_text("changed: true\n")
    with pytest.raises(ValueError, match="changed"):
        protocol(experiment.root)


def test_model_reference_must_match_its_manifest(experiment: Experiment, tmp_path: Path) -> None:
    other = tmp_path / "unrelated-model"
    other.mkdir()
    baseline = experiment.protocol.baseline
    model = replace(baseline.model, content=replace(baseline.model.content, uri=other.as_uri()))
    experiment.protocol = replace(experiment.protocol, baseline=replace(baseline, model=model))
    with pytest.raises(ValueError, match="checkpoint manifest"):
        experiment.solution("base")
