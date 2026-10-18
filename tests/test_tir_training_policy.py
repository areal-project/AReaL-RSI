"""Data admission and SFT conditioning failures, without mocking training success."""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

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
from examples.tir import distillation
from examples.tir.benchmark import Example, identity, read_examples, write_examples
from examples.tir.compositional import (
    CompositionalManifest,
    ConditionalDraw,
    DigitConstraints,
    LatticeRoutes,
    LinearSystem,
    materialize,
    propose,
    read_tasks,
    verify_task,
)
from examples.tir.distillation import (
    DistillationAttempt,
    DistillationConfig,
    PendingTeacher,
    TeacherConfig,
    TeacherDraft,
    TeacherMessage,
    boxed_answer,
    publish_prefix,
    readmit_program_outputs,
    tokenize_trajectory,
    training_events,
)
from examples.tir.experiment import CurriculumPlan, RoundRecipe
from examples.tir.synthesis import (
    CompositionalAdmissionPolicy,
    DataRecipe,
    StudentProbe,
    SynthesisManifest,
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


def test_tool_first_training_view_preserves_execution_and_masks_observations() -> None:
    original = tool_trace()
    original = (
        original[0],
        replace(original[1], content="I already know it is 2.\n```python"),
        *original[2:],
    )
    view = training_events(original, "tool_first")
    assert view[0] == original[0] and view[2:] == original[2:]
    assert "already know" not in view[1].content
    assert "already know" in original[1].content
    sample = tokenize_trajectory(
        "task", view, CharacterTokenizer(), max_length=1000, max_segment_tokens=100
    )
    conditioned = "".join(
        chr(t) for t, m in zip(sample.input_ids, sample.loss_mask, strict=True) if not m
    )
    assert conditioned == original[0].content + original[4].content
    assert training_events(original, "teacher") is original


def student_probe(
    root: Path,
    items: tuple[Example, ...],
    scores: tuple[tuple[int, ...], ...],
    *,
    traces: dict[str, tuple[TraceEvent, ...]] | None = None,
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
            traces[item.task_id] if traces is not None else (TraceEvent("generation", "answer"),),
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
    # Requires a pivot swap; the three equations uniquely yield x=2,y=3,z=4.
    assert (
        materialize(LinearSystem(((0, 1, 1), (1, 2, 0), (3, 0, 1)), (7, 8, 10))).example.answer
        == "29/9"
    )
    proposal_ref = propose(17, 18, (), tmp_path / "proposal")
    manifest = read_record(local_path(proposal_ref), CompositionalManifest)
    tasks = read_tasks(local_path(manifest.definitions))
    assert {task.example.family for task in tasks} == {
        "digit_constraints",
        "lattice_routes",
        "conditional_draws",
        "linear_system",
    }
    with pytest.raises(ValueError, match="differs"):
        verify_task(replace(tasks[0], example=replace(tasks[0].example, answer="-100")))


def test_composed_linear_system_rejects_singular_or_undefined_questions() -> None:
    with pytest.raises(ValueError, match="unique solution"):
        materialize(LinearSystem(((1, 2, 3), (2, 4, 6), (3, 6, 9)), (6, 12, 18)))
    with pytest.raises(ValueError, match="nonzero denominator"):
        materialize(LinearSystem(((1, 0, 0), (0, 1, 0), (0, 0, 1)), (1, 2, -3)))


def test_dynamic_admission_budget_covers_all_compositional_families() -> None:
    recipe = RoundRecipe(
        "Current-student admission", data=DataRecipe(real_count=80, synthetic_count=6)
    )
    policy = CompositionalAdmissionPolicy(minimum_per_family=2)
    with pytest.raises(ValueError, match="all compositional families"):
        CurriculumPlan(recipe, mixed_count=40, synthetic_proposals=192, admission_policy=policy)
    expanded = replace(recipe, data=DataRecipe(real_count=80, synthetic_count=8))
    CurriculumPlan(expanded, mixed_count=40, synthetic_proposals=192, admission_policy=policy)


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
    shared = tuple(item.task_id for item in pool[:10])
    task_order = (*shared[:4], admission.selected_ids[0], *shared[4:])
    recipe = DataRecipe(
        real_count=10,
        synthetic_count=1,
        real_task_ids=shared,
        synthetic_admission=ref,
        task_order=task_order,
    )
    data = build_dataset(
        recipe,
        pool,
        pool_ref,
        (),
        tmp_path / "training",
    )
    assert data.synthetic_count == 1 and data.real_count == 10
    control = build_dataset(
        replace(
            recipe,
            real_count=11,
            synthetic_count=0,
            synthetic_admission=None,
            real_task_ids=(*shared, pool[10].task_id),
            task_order=tuple(
                pool[10].task_id if task_id == admission.selected_ids[0] else task_id
                for task_id in task_order
            ),
        ),
        pool,
        pool_ref,
        (),
        tmp_path / "control",
    )
    mixed_rows = read_examples(local_path(data.dataset))
    control_rows = read_examples(local_path(control.dataset))
    assert mixed_rows[4] == read_examples(local_path(admission.dataset))[0]
    assert control_rows[4] == pool[10]
    assert all(mixed_rows[i] == control_rows[i] for i in range(11) if i != 4)
    restored = read_record(tmp_path / "training/manifest.json", SynthesisManifest)
    replayed = build_dataset(restored.recipe, pool, pool_ref, (), tmp_path / "replayed")
    assert replayed.dataset.digest == data.dataset.digest


def test_lattice_proposals_calibrate_constraints_without_irrelevant_obstacles(
    tmp_path: Path,
) -> None:
    proposals = propose(29, 30, (), tmp_path / "proposals", lattice_blocked_points=1)
    manifest = read_record(local_path(proposals), CompositionalManifest)
    tasks = read_tasks(local_path(manifest.definitions))
    lattice = [task for task in tasks if isinstance(task.definition, LatticeRoutes)]
    assert lattice
    for task in lattice:
        spec = task.definition
        assert isinstance(spec, LatticeRoutes) and len(spec.blocked) == 1
        x, y = spec.checkpoint
        unblocked = math.comb(x + y, x) * math.comb(
            spec.width - x + spec.height - y, spec.width - x
        )
        assert 0 < int(task.example.answer) < unblocked
    repeated = propose(29, 30, (), tmp_path / "repeated", lattice_blocked_points=1)
    second = read_record(local_path(repeated), CompositionalManifest)
    assert read_tasks(local_path(second.definitions)) == tasks


def test_composed_admission_requires_actual_correct_stdout_and_family_coverage(
    tmp_path: Path,
) -> None:
    proposals = propose(29, 30, (), tmp_path / "proposals", lattice_blocked_points=1)
    manifest = read_record(local_path(proposals), CompositionalManifest)
    items = read_examples(local_path(manifest.examples))
    traces: dict[str, tuple[TraceEvent, ...]] = {
        item.task_id: (
            TraceEvent("tool_call", "recorded execution"),
            TraceEvent("tool_result", item.answer),
            TraceEvent("generation", "final answer"),
        )
        for item in items
    }
    rejected = {item.task_id for item in items[:3]}
    traces[items[0].task_id] = (TraceEvent("generation", f"```output\n{items[0].answer}\n```"),)
    traces[items[1].task_id] = (
        TraceEvent("tool_call", "recorded execution"),
        TraceEvent("tool_result", f"Error: failed near {items[1].answer}"),
    )
    traces[items[2].task_id] = (
        TraceEvent("tool_call", "recorded execution"),
        TraceEvent("tool_result", "-100"),
    )
    probe = student_probe(tmp_path / "probe", items, ((0, 1, 0, 0),) * len(items), traces=traces)
    policy = CompositionalAdmissionPolicy(minimum_per_family=2, require_correct_tool_output=True)
    admission = admit_compositional(
        proposals,
        probe,
        count=8,
        seed=17,
        excluded_ids=(),
        output=tmp_path / "admitted",
        policy=policy,
    )
    selected = read_examples(local_path(admission.dataset))
    assert not rejected.intersection(admission.selected_ids)
    assert all(
        sum(item.family == family for item in selected) == 2
        for family in {item.family for item in items}
    )
    # A native pass without genuine stdout cannot be laundered by changing the
    # admission record: materialization rechecks the policy against the traces.
    unverified = student_probe(tmp_path / "unverified-probe", items, ((0, 1),) * len(items))
    loose = admit_compositional(
        proposals, unverified, count=1, seed=17, excluded_ids=(), output=tmp_path / "loose"
    )
    forged = write_record(
        tmp_path / "forged-admission.json",
        replace(loose, policy=CompositionalAdmissionPolicy(require_correct_tool_output=True)),
    )
    pool = tuple(real_example(index) for index in range(20))
    pool_ref = write_examples(tmp_path / "pool.jsonl", pool)
    shared_ids = tuple(item.task_id for item in pool[:10])
    with pytest.raises(ValueError, match="tool evidence"):
        build_dataset(
            DataRecipe(
                real_count=10,
                synthetic_count=1,
                synthetic_admission=forged,
                real_task_ids=shared_ids,
                task_order=(*shared_ids, loose.selected_ids[0]),
            ),
            pool,
            pool_ref,
            (),
            tmp_path / "forged-training",
        )
    # A missing family fails before publishing a partial admission directory.
    one_family = tuple(item for item in items if item.family != "lattice_routes")
    partial = student_probe(
        tmp_path / "partial", one_family, ((0, 1),) * len(one_family), traces=traces
    )
    with pytest.raises(ValueError, match="family lattice_routes"):
        admit_compositional(
            proposals,
            partial,
            count=8,
            seed=17,
            excluded_ids=(),
            output=tmp_path / "missing-family",
            policy=policy,
        )
    assert not (tmp_path / "missing-family").exists()


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


def test_teacher_rejects_service_exit_and_ambiguous_or_incomplete_answers() -> None:
    for code in ("print(2); exit()", "import sys; sys.exit(0)"):
        with pytest.raises(ValueError, match="execution service"):
            TeacherDraft("Compute a sum.", code)
    for answer in (r"\boxed{\frac{1}{2}", r"\boxed{1} or \boxed{2}"):
        with pytest.raises(ValueError, match="boxed"):
            boxed_answer(answer)
    assert boxed_answer(r"Thus $\boxed{\frac{1}{2}}$.") == r"\boxed{\frac{1}{2}}"


def test_teacher_collector_bounds_pending_work_and_persists_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Exercise the collector's real disk failure path; no training success is mocked.
    pool = tuple(real_example(index) for index in range(12))
    pool_ref = write_examples(tmp_path / "pool.jsonl", pool)
    tokenizer_dir = tmp_path / "tokenizer"
    tokenizer_dir.mkdir()
    (tokenizer_dir / "tokenizer.json").write_text("{}")
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(
            AutoTokenizer=SimpleNamespace(
                from_pretrained=lambda *args, **kwargs: CharacterTokenizer()
            )
        ),
    )
    monkeypatch.setitem(sys.modules, "prompts", SimpleNamespace(TORL_PROMPT="Question: {prompt}"))
    monkeypatch.setenv("TEST_TEACHER_KEY", "private-value")
    requests: list[tuple[TeacherMessage, ...]] = []

    def completion(
        config: TeacherConfig, messages: tuple[TeacherMessage, ...], *, json_output: bool = False
    ) -> str:
        requests.append(messages)
        return "draft" if json_output else r"\boxed{2}"

    def execution(context: PendingTeacher, response: str) -> tuple[TeacherMessage, ...]:
        context.events[:] = tool_trace()
        return (TeacherMessage("user", "observed 2"),)

    def admission(
        config: DistillationConfig,
        context: PendingTeacher,
        answer: str,
        tokenizer: CharacterTokenizer,
    ) -> DistillationAttempt:
        sample = tokenize_trajectory(
            context.example.task_id,
            tuple(context.events),
            tokenizer,
            max_length=1000,
            max_segment_tokens=100,
        )
        return DistillationAttempt(
            context.example, "teacher", tuple(context.events), True, "", sample
        )

    actual_write = write_record

    def fail_attempt_write(path: Path, value: object) -> ContentRef:
        if path.parent.name == "attempts":
            raise OSError("private-value write failed")
        return actual_write(path, value)

    monkeypatch.setattr(distillation, "teacher_completion", completion)
    monkeypatch.setattr(distillation, "execute_draft", execution)
    monkeypatch.setattr(distillation, "admit_answer", admission)
    monkeypatch.setattr(distillation, "write_record", fail_attempt_write)
    config = DistillationConfig(
        TeacherConfig("https://teacher.example/v1", "teacher", api_key_env="TEST_TEACHER_KEY"),
        pool_ref,
        (),
        str(tokenizer_dir),
        str(tmp_path / "output"),
        count=12,
        concurrency=2,
    )
    with pytest.raises(OSError, match="write failed"):
        distillation.generate(config)
    state = read_record(tmp_path / "output/state.json", distillation.CollectionState)
    assert state.status == "failed"
    assert "private-value" not in state.error
    assert "private-value" not in (tmp_path / "output/failure.log").read_text()
    assert len(requests) <= 4  # Two trajectories in flight, at most draft + final for each.
    assert not (tmp_path / "output/manifest.json").exists()


def test_teacher_cohort_uses_planned_prefix_and_preserves_closed_producer_records(
    tmp_path: Path,
) -> None:
    questions = tuple(real_example(index) for index in range(4))
    pool_ref = write_examples(tmp_path / "pool.jsonl", questions)
    producer = tmp_path / "producer"
    producer.mkdir()
    tokenizer = tmp_path / "tokenizer"
    tokenizer.mkdir()
    (tokenizer / "tokenizer.json").write_text("{}")
    config = DistillationConfig(
        TeacherConfig("https://teacher.example/v1", "teacher"),
        pool_ref,
        (),
        str(tokenizer),
        str(producer),
        count=4,
        task_ids=tuple(item.task_id for item in questions),
    )
    config_path = producer / "config.json"
    write_record(config_path, config)

    def save(index: int, *, accepted: bool) -> ContentRef:
        item = questions[index]
        sample = TokenizedTrainingSample(item.task_id, (1, 2, 3), (0, 1, 1))
        return write_record(
            producer / "attempts" / f"{item.task_id}.json",
            DistillationAttempt(
                item,
                "teacher",
                tool_trace(),
                accepted,
                "" if accepted else "incorrect",
                sample if accepted else None,
            ),
        )

    save(0, accepted=True)
    save(
        2, accepted=True
    )  # Two completed successes do not complete the first two planned questions.
    with pytest.raises(ValueError, match="not completely processed"):
        publish_prefix(config_path, 2, tmp_path / "too-early")
    assert not (tmp_path / "too-early").exists()
    rejected = save(1, accepted=False)
    before = local_path(rejected).read_bytes()
    manifest = publish_prefix(config_path, 2, tmp_path / "cohort")
    assert manifest.attempted_count == 2 and manifest.accepted_count == 1
    assert manifest.config == file_ref(config_path)
    assert read_training_samples(local_path(manifest.dataset))[0].task_id == questions[0].task_id
    assert local_path(rejected).read_bytes() == before
    with pytest.raises(FileExistsError):
        publish_prefix(config_path, 2, tmp_path / "cohort")


@pytest.mark.skipif(
    importlib.util.find_spec("math_verify") is None,
    reason="Independent math verification runs in the AReaL backend environment",
)
def test_program_output_admission_rejects_correct_prose_after_wrong_execution(
    tmp_path: Path,
) -> None:
    questions = tuple(replace(real_example(index), answer="2") for index in range(2))
    pool = write_examples(tmp_path / "pool.jsonl", questions)
    tokenizer = tmp_path / "tokenizer"
    tokenizer.mkdir()
    (tokenizer / "tokenizer.json").write_text("{}")
    producer = tmp_path / "producer"
    producer.mkdir()
    config = DistillationConfig(
        TeacherConfig("https://teacher.example/v1", "teacher"),
        pool,
        (),
        str(tokenizer),
        str(producer),
        count=2,
        task_ids=tuple(item.task_id for item in questions),
    )
    config_ref = write_record(producer / "config.json", config)
    correct = tool_trace()
    wrong = (
        *correct[:2],
        TraceEvent("generation", "\nprint(-2)\n```"),
        TraceEvent("tool_call", "```python\nprint(-2)\n```"),
        TraceEvent("tool_result", "\n```output\n-2\n```\n"),
        correct[-1],
    )
    refs = []
    for question, events in zip(questions, (wrong, correct), strict=True):
        sample = tokenize_trajectory(
            question.task_id, events, CharacterTokenizer(), max_length=1000, max_segment_tokens=100
        )
        refs.append(
            write_record(
                producer / "attempts" / f"{question.task_id}.json",
                DistillationAttempt(question, "teacher", events, True, "", sample),
            )
        )
    distillation._publish(
        config_ref, questions, (file_ref(tokenizer / "tokenizer.json"),), tuple(refs), producer
    )
    before = tuple(local_path(ref).read_bytes() for ref in refs)
    manifest = readmit_program_outputs(producer / "manifest.json", tmp_path / "readmitted")
    assert manifest.attempted_count == 2 and manifest.accepted_count == 1
    assert tuple(x.task_id for x in read_training_samples(local_path(manifest.dataset))) == (
        questions[1].task_id,
    )
    rejected = read_record(local_path(manifest.attempts[0]), DistillationAttempt)
    assert not rejected.accepted and rejected.sample is None
    assert "stdout" in rejected.error and rejected.events == wrong
    assert tuple(local_path(ref).read_bytes() for ref in refs) == before
    pending = PendingTeacher(questions[0], "answer", list(wrong[:-1]))
    with pytest.raises(ValueError, match="stdout"):
        distillation.admit_answer(config, pending, r"\boxed{2}", CharacterTokenizer())
