"""Research protocol failures and synthesis behavior, without GPU/model simulations."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_training import git_repository

from areal_rsi.api.control import encode_record
from areal_rsi.api.execution import AReaLRuntime, TrainingResult
from areal_rsi.api.records import (
    BenchmarkSplit,
    EvaluationReport,
    EvaluationResult,
    EvaluationSample,
    TraceEvent,
)
from areal_rsi.infra.artifacts import file_ref, write_record
from examples.tir.benchmark import Benchmark, Example, identity, read_examples, write_examples
from examples.tir.pipeline import ExperimentConfig
from examples.tir.study import Candidate, Study, StudyConfig, paired, prepare, protocol
from examples.tir.synthesis import (
    DataRecipe,
    FamilyWeight,
    SynthesisManifest,
    SynthesisPolicy,
    build_dataset,
)


def example(prompt: str, *, steps: int = 2) -> Example:
    return Example(identity(prompt), prompt, "4", "gsm8k-train", "<<2+2=4>>" * steps)


def test_dataset_preserves_unicode_separators_inside_json_strings(tmp_path: Path) -> None:
    item = example("Two cats\u2028and two dogs\x85How many animals?")
    path = tmp_path / "data.jsonl"
    write_examples(path, (item,))
    assert read_examples(path) == (item,)


def evaluation(path: Path, passed: tuple[bool, ...]) -> EvaluationResult:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as output:
        for index, success in enumerate(passed):
            sample = EvaluationSample(
                str(index), "test", float(success), success, (TraceEvent("generation", "test"),)
            )
            output.write(json.dumps(encode_record(sample)) + "\n")
    report = EvaluationReport(
        path.stem, BenchmarkSplit.VALIDATE, all(passed), sum(passed) / len(passed), {}, "fixture"
    )
    return EvaluationResult("test", report, file_ref(path), ())


def test_word_problem_mixture_reproducible_verified_and_train_only(tmp_path: Path) -> None:
    pool = tuple(example(f"Training question {i}", steps=i) for i in range(6))
    ref = write_examples(tmp_path / "pool.jsonl", pool)
    recipe = DataRecipe(
        real_count=2,
        synthetic_count=80,
        real_min_steps=3,
        synthesis=SynthesisPolicy((FamilyWeight("ratio"),), 2, 4),
    )
    first = build_dataset(recipe, pool, ref, (), tmp_path / "first")
    second = build_dataset(recipe, pool, ref, (), tmp_path / "second")
    assert first.dataset.digest == second.dataset.digest
    items = read_examples(tmp_path / "first/train.jsonl")
    generated = [item for item in items if item.source == "verified-synthetic"]
    assert len(items) == 82 and len(generated) == 80
    assert all(item.family == "ratio" and 2 <= item.difficulty <= 4 for item in generated)
    assert all(item.reference_solution.count("<<") >= 3 for item in items if item not in generated)
    with pytest.raises(ValueError, match="Held-out"):
        build_dataset(recipe, pool, ref, (pool[0].task_id,), tmp_path / "leaked")
    with pytest.raises(ValueError, match="Only official training"):
        build_dataset(recipe, (replace(pool[0], source="gsm8k-test"),), ref, (), tmp_path / "test")
    with pytest.raises(ValueError, match="eligible"):
        build_dataset(replace(recipe, real_count=6), pool, ref, (), tmp_path / "excess")


def test_paired_statistics_align_tasks_and_refuse_tampering(tmp_path: Path) -> None:
    baseline = evaluation(tmp_path / "base.jsonl", (False, False, False, False, True, True))
    candidate = evaluation(tmp_path / "candidate.jsonl", (True, True, True, True, False, True))
    result = paired(baseline, candidate)
    assert (result.wins, result.losses, result.both_correct, result.both_wrong) == (4, 1, 1, 0)
    assert result.delta == 0.5 and result.mcnemar_exact_p == 0.375
    assert result.ci95_low <= result.delta <= result.ci95_high
    shorter = evaluation(tmp_path / "shorter.jsonl", (True, False))
    with pytest.raises(ValueError, match="same tasks"):
        paired(baseline, shorter)
    Path(tmp_path / "candidate.jsonl").write_text("tampered")
    with pytest.raises(ValueError, match="changed"):
        paired(baseline, candidate)


@pytest.fixture
def study(tmp_path: Path) -> Study:
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
    dev = write_examples(tmp_path / "dev.jsonl", (example("dev"),))
    final = write_examples(
        tmp_path / "final.jsonl", (replace(example("test"), source="gsm8k-test"),)
    )
    manifest = Benchmark(17, train, final, train, dev, final, (identity("dev"), identity("test")))
    write_record(tmp_path / "benchmark.json", manifest)
    config = StudyConfig(
        ExperimentConfig(AReaLRuntime(str(backend), "/unused/python"), str(model)),
        str(tmp_path / "benchmark.json"),
    )
    root = tmp_path / "study"
    prepare(config, root)
    return Study(root)


def test_selection_requires_multiple_rounds_best_dev_and_is_immutable(study: Study) -> None:
    root, frozen = study.root, study.protocol
    base = evaluation(root / "base-samples.jsonl", (True, False, False, False))
    write_record(root / "dev/base.json", base)
    recipe_ref = write_record(root / "recipe.json", DataRecipe())
    data = SynthesisManifest(DataRecipe(), frozen.benchmark.train, frozen.benchmark.train, 1, 0)

    def candidate(name: str, passed: tuple[bool, ...]) -> None:
        dev = evaluation(root / f"{name}-samples.jsonl", passed)
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
            dev,
        )
        write_record(root / f"rounds/{name}/candidate.json", record)

    candidate("r1", (True, True, False, False))
    with pytest.raises(ValueError, match="minimum number"):
        study.select("r1")
    candidate("r2", (True, True, True, False))
    with pytest.raises(ValueError, match="best dev"):
        study.select("r1")
    selected = study.select("r2")
    assert selected.dev_comparison.delta == 0.5 and selected.completed_rounds == ("r1", "r2")
    with pytest.raises(ValueError, match="immutable"):
        study.select("r1")
    with pytest.raises(ValueError, match="Selection is frozen"):
        asyncio.run(study.evaluate("r1"))
    with pytest.raises(ValueError, match="restricted"):
        asyncio.run(study.evaluate("r1", final=True))


def test_frozen_protocol_rejects_data_and_source_changes(study: Study) -> None:
    source = Path(study.config.runtime.repository) / "examples/tir/tir_math_config.yaml"
    source.write_text("changed: true\n")
    with pytest.raises(ValueError, match="changed"):
        protocol(study.root)
