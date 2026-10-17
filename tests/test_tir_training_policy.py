"""Data admission and SFT conditioning failures, without mocking training success."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from areal_rsi.api.control import encode_record
from areal_rsi.api.execution import TokenizedTrainingSample
from areal_rsi.api.records import (
    BenchmarkSplit,
    ContentRef,
    EvaluationReport,
    EvaluationResult,
    EvaluationSample,
    TraceEvent,
)
from areal_rsi.infra.artifacts import file_ref, local_path, read_record, write_record
from areal_rsi.infra.training import read_training_samples
from examples.tir.benchmark import Example, identity, read_examples, write_examples
from examples.tir.compositional import (
    CompositionalManifest,
    ConditionalDraw,
    DigitConstraints,
    LatticeRoutes,
    materialize,
    propose,
    read_tasks,
    verify_task,
)
from examples.tir.distillation import tokenize_trajectory
from examples.tir.experiment import RoundRecipe
from examples.tir.synthesis import (
    DataRecipe,
    StudentProbe,
    admit_compositional,
    build_dataset,
    dynamic_curriculum,
    normalize_gold,
    probe_groups,
)


class CharacterTokenizer:
    eos_token_id = 0

    def encode(self, text: str, *, add_special_tokens: bool) -> list[int]:
        assert not add_special_tokens
        return list(map(ord, text))


def tool_trace() -> tuple[TraceEvent, ...]:
    return (
        TraceEvent("prompt", "Solve 1+1:"),
        TraceEvent("generation", "Compute the sum.\n```python"),
        TraceEvent("generation", "\nprint(1+1)\n```"),
        TraceEvent("tool_call", "```python\nprint(1+1)\n```"),
        TraceEvent("tool_result", "\n```output\n2\n```\n"),
        TraceEvent("generation", "\nThe answer is \\boxed{2}."),
    )


def test_sft_masks_actual_observations_and_does_not_duplicate_tool_calls(tmp_path: Path) -> None:
    events = tool_trace()
    sample = tokenize_trajectory(
        "task", events, CharacterTokenizer(), max_length=1000, max_segment_tokens=100
    )
    conditioned = "".join(
        chr(token)
        for token, mask in zip(sample.input_ids, sample.loss_mask, strict=True)
        if not mask
    )
    learned = "".join(
        chr(token) for token, mask in zip(sample.input_ids, sample.loss_mask, strict=True) if mask
    )
    assert conditioned == events[0].content + events[4].content
    assert learned == events[1].content + events[2].content + events[5].content + "\0"
    path = tmp_path / "targets.jsonl"
    path.write_text(json.dumps(encode_record(sample)) + "\n")
    assert read_training_samples(path) == (sample,)
    path.write_text(path.read_text() * 2)
    with pytest.raises(ValueError, match="distinct"):
        read_training_samples(path)


def test_sft_rejects_overflow_mismatched_execution_and_tool_answer_leakage() -> None:
    tokenizer = CharacterTokenizer()
    events = tool_trace()
    with pytest.raises(ValueError, match="context token budget"):
        tokenize_trajectory("task", events, tokenizer, max_length=20, max_segment_tokens=100)
    with pytest.raises(ValueError, match="generation token budget"):
        tokenize_trajectory("task", events, tokenizer, max_length=1000, max_segment_tokens=5)
    changed = (*events[:3], TraceEvent("tool_call", "```python\nprint(5)\n```"), *events[4:])
    with pytest.raises(ValueError, match="differs"):
        tokenize_trajectory("task", changed, tokenizer, max_length=1000, max_segment_tokens=100)
    leaked = (*events[:4], TraceEvent("tool_result", "\n```output\n\\boxed{2}\n```\n"), events[5])
    with pytest.raises(ValueError, match="Only the post-execution"):
        tokenize_trajectory("task", leaked, tokenizer, max_length=1000, max_segment_tokens=100)
    with pytest.raises(ValueError, match="conditioning prefix"):
        TokenizedTrainingSample("task", (1, 2), (1, 1))


def student_probe(
    root: Path, items: tuple[Example, ...], scores: tuple[tuple[int, ...], ...]
) -> ContentRef:
    repeated = tuple(
        replace(item, repetitions=len(group)) for item, group in zip(items, scores, strict=True)
    )
    examples = write_examples(root / "questions.jsonl", repeated)
    samples = tuple(
        EvaluationSample(
            f"{item.task_id}-rep{index}",
            "answer",
            float(score),
            bool(score),
            (TraceEvent("generation", "answer"),),
        )
        for item, group in zip(items, scores, strict=True)
        for index, score in enumerate(group)
    )
    path = root / "samples.jsonl"
    path.write_text("".join(json.dumps(encode_record(item)) + "\n" for item in samples))
    accuracy = sum(item.score for item in samples) / len(samples)
    result = EvaluationResult(
        "probe",
        EvaluationReport(
            "student",
            BenchmarkSplit.TRAIN,
            all(x.passed for x in samples),
            accuracy,
            {"accuracy": accuracy, "num_tasks": float(len(samples))},
            "fixture",
        ),
        file_ref(path),
        (),
    )
    return write_record(
        root / "probe.json",
        StudentProbe(ContentRef("file:///model", "model-digest"), examples, result),
    )


def real_example(index: int) -> Example:
    prompt = f"Compute {index}+1."
    return Example(identity(prompt), prompt, str(index + 1), "torl-train")


def test_dynamic_course_reselects_mixed_groups_and_retains_random_coverage(tmp_path: Path) -> None:
    pool = tuple(real_example(i) for i in range(20))
    pool_ref = write_examples(tmp_path / "pool.jsonl", pool)
    probe_ref = student_probe(
        tmp_path / "probe", pool[:3], ((0, 1, 0, 1), (0, 0, 0, 0), (1, 1, 1, 1))
    )
    selected = dynamic_curriculum(
        pool_ref, probe_ref, mixed_count=1, random_count=5, seed=17, excluded_ids=()
    )
    assert selected.mixed_task_ids == (pool[0].task_id,)
    assert len(selected.random_task_ids) == 5
    assert set(selected.random_task_ids).isdisjoint(selected.mixed_task_ids)
    ref = write_record(tmp_path / "curriculum.json", selected)
    data = build_dataset(
        DataRecipe(real_count=6, curriculum=ref), pool, pool_ref, (), tmp_path / "data"
    )
    assert {x.task_id for x in read_examples(local_path(data.dataset))} == set(
        (*selected.mixed_task_ids, *selected.random_task_ids)
    )
    forged = write_record(
        tmp_path / "forged.json", replace(selected, mixed_task_ids=(pool[1].task_id,))
    )
    with pytest.raises(ValueError, match="mixed evidence"):
        build_dataset(
            DataRecipe(real_count=6, curriculum=forged), pool, pool_ref, (), tmp_path / "bad"
        )
    with pytest.raises(ValueError, match=r"held-out|independent"):
        dynamic_curriculum(
            pool_ref,
            probe_ref,
            mixed_count=1,
            random_count=5,
            seed=17,
            excluded_ids=(pool[0].task_id,),
        )


def test_student_probe_rejects_incomplete_or_changed_evidence(tmp_path: Path) -> None:
    ref = student_probe(tmp_path, (real_example(1),), ((0, 1, 0, 1),))
    probe = read_record(local_path(ref), StudentProbe)
    assert probe_groups(probe)[0].mixed
    path = local_path(probe.evaluation.samples)
    path.write_text("\n".join(path.read_text().splitlines()[:-1]) + "\n")
    with pytest.raises(ValueError, match=r"changed|digest"):
        probe_groups(probe)


def test_composed_tasks_use_multiple_constraints_and_independent_exact_solvers(
    tmp_path: Path,
) -> None:
    assert materialize(DigitConstraints(2, 5, 3, 2, 3)).example.answer == "4"
    assert materialize(LatticeRoutes(2, 2, (1, 1), ((0, 1),))).example.answer == "2"
    assert materialize(ConditionalDraw(2, 2, 1, 2, 1, 1, 2, 0)).example.answer == "4/5"
    proposal_ref = propose(17, 18, (), tmp_path / "proposal")
    manifest = read_record(local_path(proposal_ref), CompositionalManifest)
    tasks = read_tasks(local_path(manifest.definitions))
    assert {task.example.family for task in tasks} == {
        "digit_constraints",
        "lattice_routes",
        "conditional_draws",
    }
    with pytest.raises(ValueError, match="differs"):
        verify_task(replace(tasks[0], example=replace(tasks[0].example, answer="-100")))


def test_composed_admission_rejects_easy_and_unsolved_groups_and_caps_mixture(
    tmp_path: Path,
) -> None:
    proposal_ref = propose(29, 6, (), tmp_path / "proposal")
    manifest = read_record(local_path(proposal_ref), CompositionalManifest)
    proposed = read_examples(local_path(manifest.examples))
    # Training-only gold normalization may wrap a fraction without changing its value.
    probed = tuple(
        replace(x, answer=normalize_gold(x.answer, "math_delimited")) for x in proposed[:3]
    )
    probe_ref = student_probe(
        tmp_path / "probe", probed, ((0, 1, 0, 1), (1, 1, 1, 1), (0, 0, 0, 0))
    )
    admission = admit_compositional(
        proposal_ref, probe_ref, count=1, seed=17, excluded_ids=(), output=tmp_path / "admitted"
    )
    assert admission.selected_ids == (proposed[0].task_id,)
    with pytest.raises(ValueError, match="Insufficient"):
        admit_compositional(
            proposal_ref, probe_ref, count=2, seed=17, excluded_ids=(), output=tmp_path / "too-many"
        )
    ref = file_ref(tmp_path / "admitted/admission.json")
    with pytest.raises(ValueError, match="small"):
        DataRecipe(real_count=5, synthetic_count=1, synthetic_admission=ref)
    pool = tuple(real_example(i) for i in range(20))
    pool_ref = write_examples(tmp_path / "pool.jsonl", pool)
    data = build_dataset(
        DataRecipe(real_count=10, synthetic_count=1, synthetic_admission=ref),
        pool,
        pool_ref,
        (),
        tmp_path / "training",
    )
    assert data.synthetic_count == 1 and data.real_count == 10


def test_sft_and_grpo_recipes_have_distinct_data_inputs() -> None:
    manifest = ContentRef("file:///teacher/manifest.json", "digest")
    recipe = RoundRecipe(
        "Learn verified executable trajectories", algorithm="sft", data=None, distillation=manifest
    )
    assert recipe.algorithm == "sft"
    with pytest.raises(ValueError, match="set question data to null"):
        RoundRecipe("Ambiguous inputs", algorithm="sft", distillation=manifest)
    with pytest.raises(ValueError, match="SFT requires"):
        RoundRecipe("Missing demonstrations", algorithm="sft", data=None)
