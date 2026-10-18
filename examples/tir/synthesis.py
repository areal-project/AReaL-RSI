"""Editable TIR research policy: verified math tasks mixed with training-only ToRL.

The Leader may change these generators and recipes between rounds. Each generated
dataset is immutable and its source archive is recorded by the experiment pipeline.
There are fifteen families.  Synthetic difficulty currently changes parameter
ranges; it is a controllable curriculum proxy, not a calibrated reasoning scale.
Family weights are proposal weights: deduplication and retry limits mean that
the final family counts need not match them.  New training material may put
bare mathematical gold inside delimiters for MathVerify without changing the
question or its mathematical value.
"""

from __future__ import annotations

import json
import math
import random
import re
from dataclasses import dataclass, field, replace
from fractions import Fraction
from itertools import combinations
from pathlib import Path
from typing import Literal

from areal_rsi.api.control import decode_record
from areal_rsi.api.records import BenchmarkSplit, ContentRef, EvaluationResult
from areal_rsi.infra.artifacts import file_ref, local_path, read_record, write_record
from areal_rsi.infra.coordination.evaluation import read_evaluation_samples
from examples.tir.benchmark import Example, identity, read_examples, write_examples

Family = Literal[
    "inventory",
    "rates",
    "budget",
    "ratio",
    "growth",
    "percent",
    "average",
    "units",
    "work",
    "decimal",
    "fraction",
    "radical",
    "linear",
    "counting",
    "sequence",
]
GoldFormat = Literal["preserve", "math_delimited"]


@dataclass(frozen=True, slots=True)
class FamilyWeight:
    family: Family
    weight: int = 1

    def __post_init__(self) -> None:
        if self.weight < 1:
            raise ValueError("Family weights must be positive")


def _default_family_weights() -> tuple[FamilyWeight, ...]:
    families: tuple[tuple[Family, int], ...] = (
        ("inventory", 1),
        ("rates", 1),
        ("budget", 1),
        ("ratio", 1),
        ("growth", 1),
        # These families target the arithmetic chains seen in valid failures
        # while remaining independent of held-out wording.
        ("percent", 2),
        ("average", 2),
        ("units", 2),
        ("work", 2),
        ("decimal", 2),
        ("fraction", 2),
        ("radical", 2),
        ("linear", 2),
        ("counting", 2),
        ("sequence", 2),
    )
    return tuple(FamilyWeight(family, weight) for family, weight in families)


@dataclass(frozen=True, slots=True)
class SynthesisPolicy:
    families: tuple[FamilyWeight, ...] = field(default_factory=_default_family_weights)
    min_difficulty: int = 1
    max_difficulty: int = 3
    difficulty_weights: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not self.families or len({item.family for item in self.families}) != len(self.families):
            raise ValueError("Supply distinct nonempty synthesis families")
        if not 1 <= self.min_difficulty <= self.max_difficulty <= 5:
            raise ValueError("Synthetic difficulty must be between 1 and 5")
        if self.difficulty_weights and len(self.difficulty_weights) != (
            self.max_difficulty - self.min_difficulty + 1
        ):
            raise ValueError("Difficulty weights must cover the configured difficulty range")
        if any(weight < 1 for weight in self.difficulty_weights):
            raise ValueError("Difficulty weights must be positive")


@dataclass(frozen=True, slots=True)
class DataRecipe:
    seed: int = 17
    real_count: int = 1024
    synthetic_count: int = 0
    synthesis: SynthesisPolicy = field(default_factory=SynthesisPolicy)
    # The native MathVerify worker parses mathematical expressions reliably
    # when bare LaTeX is delimited.  This is a data-boundary policy: it only
    # affects newly materialized training JSONL, never the official pool.
    # Preserve legacy recipe behavior; rounds opt into normalization explicitly.
    gold_format: GoldFormat = "preserve"
    # An optional exact curriculum. IDs are resolved against the frozen official
    # training pool at materialization time; an empty tuple preserves legacy
    # random sampling behavior.
    real_task_ids: tuple[str, ...] = ()
    curriculum: ContentRef | None = None
    synthetic_admission: ContentRef | None = None

    def __post_init__(self) -> None:
        if min(self.real_count, self.synthetic_count) < 0:
            raise ValueError("Dataset counts must be nonnegative")
        if not 1 <= self.real_count + self.synthetic_count <= 20000:
            raise ValueError("A research round requires 1..20000 distinct training examples")
        if self.gold_format not in {"preserve", "math_delimited"}:
            raise ValueError("Unknown training gold format")
        if len(set(self.real_task_ids)) != len(self.real_task_ids):
            raise ValueError("Focused training task IDs must be unique")
        if self.real_task_ids and len(self.real_task_ids) != self.real_count:
            raise ValueError("Focused training task IDs must match real_count")
        if self.curriculum is not None and self.real_task_ids:
            raise ValueError("Use the recorded dynamic curriculum or explicit IDs, not both")
        if self.synthetic_admission is not None and (
            self.synthetic_count <= 0 or self.synthetic_count * 10 > self.real_count
        ):
            raise ValueError(
                "Student-admitted synthetic data must be a small, at most 1:10 mixture"
            )


@dataclass(frozen=True, slots=True)
class StudentProbe:
    model: ContentRef
    examples: ContentRef
    evaluation: EvaluationResult


@dataclass(frozen=True, slots=True)
class ProbeGroup:
    task_id: str
    successes: int
    samples: int

    @property
    def mixed(self) -> bool:
        return 0 < self.successes < self.samples


@dataclass(frozen=True, slots=True)
class DynamicCurriculum:
    training_pool: ContentRef
    probe: ContentRef
    seed: int
    mixed_task_ids: tuple[str, ...]
    random_task_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CompositionalAdmissionPolicy:
    minimum_per_family: int = 0
    require_correct_tool_output: bool = False

    def __post_init__(self) -> None:
        if self.minimum_per_family < 0:
            raise ValueError("Synthetic family minimum must be nonnegative")


@dataclass(frozen=True, slots=True)
class SyntheticAdmission:
    proposals: ContentRef
    probe: ContentRef
    dataset: ContentRef
    selected_ids: tuple[str, ...]
    policy: CompositionalAdmissionPolicy = field(default_factory=CompositionalAdmissionPolicy)


@dataclass(frozen=True, slots=True)
class CompositionalCandidates:
    examples: tuple[Example, ...]
    eligible_ids: tuple[str, ...]


def probe_groups(probe: StudentProbe) -> tuple[ProbeGroup, ...]:
    items = read_examples(local_path(probe.examples))
    if any(item.repetitions < 2 for item in items):
        raise ValueError("Student admission requires repeated samples for every question")
    from examples.tir.benchmark import evaluation_cases

    cases = evaluation_cases(items)
    samples = read_evaluation_samples(
        probe.evaluation,
        task_ids=tuple(item.task_id for item in cases),
        split=BenchmarkSplit.TRAIN,
    )
    by_id = {item.task_id: item for item in samples}
    groups = []
    for item in items:
        scores = tuple(by_id[f"{item.task_id}-rep{i}"].score for i in range(item.repetitions))
        if any(score not in (0, 1) for score in scores) or any(
            by_id[f"{item.task_id}-rep{i}"].passed != bool(scores[i])
            for i in range(item.repetitions)
        ):
            raise ValueError("Student admission requires binary correctness evidence")
        groups.append(ProbeGroup(item.task_id, int(sum(scores)), len(scores)))
    return tuple(groups)


def dynamic_curriculum(
    pool_ref: ContentRef,
    probe_ref: ContentRef,
    *,
    mixed_count: int,
    random_count: int,
    seed: int,
    excluded_ids: tuple[str, ...],
) -> DynamicCurriculum:
    if min(mixed_count, random_count) < 1:
        raise ValueError("A dynamic curriculum requires both mixed and random real questions")
    pool = read_examples(local_path(pool_ref))
    excluded = set(excluded_ids)
    if any(item.source != "torl-train" or item.task_id in excluded for item in pool):
        raise ValueError("Dynamic curricula may only use the official independent training pool")
    probe = read_record(local_path(probe_ref), StudentProbe)
    by_id = {item.task_id: item for item in pool}
    for item in read_examples(local_path(probe.examples)):
        original = by_id.get(item.task_id)
        if original is None or item.prompt != original.prompt or item.source != "torl-train":
            raise ValueError("Student probe includes a question outside the frozen training pool")
        if item.answer not in {original.answer, normalize_gold(original.answer, "math_delimited")}:
            raise ValueError("Student probe has modified official mathematical gold")
    mixed = [group.task_id for group in probe_groups(probe) if group.mixed]
    if len(mixed) < mixed_count:
        raise ValueError("Probe has insufficient mixed questions; sample more or reduce the budget")
    rng = random.Random(seed)
    focused = tuple(rng.sample(mixed, mixed_count))
    remaining = [item.task_id for item in pool if item.task_id not in set(focused)]
    return DynamicCurriculum(
        pool_ref, probe_ref, seed, focused, tuple(rng.sample(remaining, random_count))
    )


def _compositional_candidates(
    proposals: ContentRef,
    probe_ref: ContentRef,
    policy: CompositionalAdmissionPolicy,
    excluded_ids: tuple[str, ...],
) -> CompositionalCandidates:
    from examples.tir.compositional import CompositionalManifest, read_tasks

    manifest = read_record(local_path(proposals), CompositionalManifest)
    tasks = read_tasks(local_path(manifest.definitions))
    examples = read_examples(local_path(manifest.examples))
    if tuple(task.example for task in tasks) != examples or len(tasks) != manifest.count:
        raise ValueError("Proposal definitions and questions differ")
    probe = read_record(local_path(probe_ref), StudentProbe)
    probed = read_examples(local_path(probe.examples))
    by_id = {item.task_id: item for item in examples}
    for item in probed:
        original = by_id.get(item.task_id)
        if (
            original is None
            or replace(item, repetitions=1, answer=original.answer) != original
            or item.answer
            not in {original.answer, normalize_gold(original.answer, "math_delimited")}
        ):
            raise ValueError("Student probe does not match independently verified proposals")
    eligible = [group.task_id for group in probe_groups(probe) if group.mixed]
    if set(by_id) & set(excluded_ids):
        raise ValueError("Synthetic proposals overlap protected benchmark identities")
    if policy.require_correct_tool_output:
        from examples.tir.benchmark import evaluation_cases

        cases = evaluation_cases(probed)
        samples = read_evaluation_samples(
            probe.evaluation,
            task_ids=tuple(item.task_id for item in cases),
            split=BenchmarkSplit.TRAIN,
        )
        by_sample = {sample.task_id: sample for sample in samples}
        verified: set[str] = set()
        for item in probed:
            for repetition in range(item.repetitions):
                sample = by_sample[f"{item.task_id}-rep{repetition}"]
                if not sample.passed:
                    continue
                last_tool = max(
                    (i for i, event in enumerate(sample.events) if event.kind == "tool_result"),
                    default=-1,
                )
                if last_tool < 1 or sample.events[last_tool - 1].kind != "tool_call":
                    continue
                # These exact solvers produce integers or rational numbers. Only
                # a complete scalar stdout counts; numbers inside errors or the
                # model's own generated output fences cannot establish reachability.
                try:
                    if Fraction(sample.events[last_tool].content.strip()) == Fraction(
                        by_id[item.task_id].answer
                    ):
                        verified.add(item.task_id)
                except (ValueError, ZeroDivisionError):
                    continue
        eligible = [task_id for task_id in eligible if task_id in verified]
    return CompositionalCandidates(examples, tuple(eligible))


def admit_compositional(
    proposals: ContentRef,
    probe_ref: ContentRef,
    *,
    count: int,
    seed: int,
    excluded_ids: tuple[str, ...],
    output: Path,
    policy: CompositionalAdmissionPolicy | None = None,
) -> SyntheticAdmission:
    if count < 1:
        raise ValueError("Synthetic admission must request a positive count")
    policy = policy or CompositionalAdmissionPolicy()
    candidates = _compositional_candidates(proposals, probe_ref, policy, excluded_ids)
    by_id = {item.task_id: item for item in candidates.examples}
    eligible = candidates.eligible_ids
    if len(eligible) < count:
        raise ValueError("Insufficient independently verified, student-mixed synthetic tasks")
    families = sorted({item.family for item in candidates.examples if item.family is not None})
    if policy.minimum_per_family * len(families) > count:
        raise ValueError("Synthetic count cannot cover the requested family minimum")
    rng = random.Random(seed)
    selected_ids: list[str] = []
    for family in families:
        available = [task_id for task_id in eligible if by_id[task_id].family == family]
        if len(available) < policy.minimum_per_family:
            raise ValueError(f"Insufficient student-admitted tasks for family {family}")
        selected_ids.extend(rng.sample(available, policy.minimum_per_family))
    remaining = [task_id for task_id in eligible if task_id not in set(selected_ids)]
    selected_ids.extend(rng.sample(remaining, count - len(selected_ids)))
    if policy.minimum_per_family:
        rng.shuffle(selected_ids)
    selected = tuple(selected_ids)
    output.mkdir(parents=True, exist_ok=False)
    dataset = write_examples(
        output / "admitted.jsonl", tuple(by_id[task_id] for task_id in selected)
    )
    admission = SyntheticAdmission(proposals, probe_ref, dataset, selected, policy)
    write_record(output / "admission.json", admission)
    return admission


@dataclass(frozen=True, slots=True)
class SynthesisManifest:
    recipe: DataRecipe
    training_pool: ContentRef
    dataset: ContentRef
    real_count: int
    synthetic_count: int


@dataclass(frozen=True, slots=True)
class MixedRolloutEvidence:
    task_id: str
    rollout_file: ContentRef
    logged_samples: int
    rewards: tuple[float, ...]
    complete: bool


@dataclass(frozen=True, slots=True)
class LoggedSample:
    """The logger fields needed by the rollout curriculum selector."""

    sample_idx: int
    reward: float
    prompt: str


@dataclass(frozen=True, slots=True)
class RolloutCurriculumSelection:
    rule: str
    frozen_pool: ContentRef
    trained_dataset: ContentRef
    rollout_files: tuple[ContentRef, ...]
    extractor_source: ContentRef
    rollout_file_count: int
    nonempty_group_count: int
    empty_file_count: int
    complete_mixed_group_count: int
    partial_mixed_group_count: int
    selected_task_ids: tuple[str, ...]
    evidence: tuple[MixedRolloutEvidence, ...]


def select_mixed_rollout_curriculum(
    frozen_pool: tuple[Example, ...],
    trained_data: tuple[Example, ...],
    rollout_files: tuple[Path, ...],
    *,
    frozen_pool_source: ContentRef,
    trained_dataset_source: ContentRef,
    n_samples: int,
    excluded_ids: tuple[str, ...] = (),
) -> RolloutCurriculumSelection:
    """Select official questions with 0/1 variance in one actual rollout group."""
    if n_samples < 2:
        raise ValueError("Rollout curriculum requires at least two samples")
    if len(set(path.resolve() for path in rollout_files)) != len(rollout_files):
        raise ValueError("Rollout files must be unique")
    if not rollout_files:
        raise ValueError("Rollout curriculum requires actual rollout files")
    if not isinstance(n_samples, int) or isinstance(n_samples, bool):
        raise ValueError("Rollout sample count must be an integer")
    pool_from_source = read_examples(local_path(frozen_pool_source))
    if pool_from_source != frozen_pool:
        raise ValueError("Frozen pool source does not match the supplied pool")
    trained_from_source = read_examples(local_path(trained_dataset_source))
    if trained_from_source != trained_data:
        raise ValueError("Trained dataset source does not match the supplied data")
    pool_by_id = {item.task_id: item for item in frozen_pool}
    if len(pool_by_id) != len(frozen_pool) or any(
        item.source != "torl-train" for item in frozen_pool
    ):
        raise ValueError("Focused curriculum requires a unique official ToRL training pool")
    excluded = set(excluded_ids)
    trained_by_id = {item.task_id: item for item in trained_data}
    if len(trained_by_id) != len(trained_data):
        raise ValueError("Trained data contains duplicate task IDs")
    if any(item.source not in {"torl-train", "verified-synthetic"} for item in trained_data):
        raise ValueError("Trained data contains a non-training example")
    if any(
        item.source == "torl-train"
        and (
            item.task_id not in pool_by_id
            or pool_by_id[item.task_id].prompt != item.prompt
            or item.answer
            not in {
                pool_by_id[item.task_id].answer,
                normalize_gold(pool_by_id[item.task_id].answer, "math_delimited"),
            }
        )
        for item in trained_data
    ):
        raise ValueError("Official trained data must match the frozen pool")
    evidence: list[MixedRolloutEvidence] = []
    nonempty = empty = complete_mixed = partial_mixed = 0
    source_refs = tuple(file_ref(path) for path in rollout_files)
    for path, source in zip(rollout_files, source_refs, strict=True):
        samples: list[LoggedSample] = []
        with path.open() as stream:
            for line_number, line in enumerate(stream, 1):
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(f"Invalid rollout JSON at {path}:{line_number}") from error
                if not isinstance(raw, dict):
                    raise ValueError(f"Rollout row is not an object at {path}:{line_number}")
                required = ("sample_idx", "reward", "prompt")
                if any(name not in raw for name in required):
                    raise ValueError(
                        f"Rollout row is missing a required field at {path}:{line_number}"
                    )
                try:
                    logged = decode_record(
                        LoggedSample,
                        {name: raw[name] for name in required},
                    )
                except ValueError as error:
                    raise ValueError(f"Rollout group has invalid logger fields: {path}") from error
                if isinstance(logged.sample_idx, bool) or not 0 <= logged.sample_idx < n_samples:
                    raise ValueError(f"Rollout group has invalid sample index: {path}")
                if isinstance(logged.reward, bool) or not isinstance(logged.reward, (int, float)):
                    raise ValueError(f"Rollout group has invalid reward: {path}")
                if not math.isfinite(logged.reward) or logged.reward not in (0.0, 1.0):
                    raise ValueError(f"Rollout group has invalid reward: {path}")
                samples.append(logged)
        if not samples:
            empty += 1
            continue
        nonempty += 1
        if len(samples) > n_samples or len({sample.sample_idx for sample in samples}) != len(
            samples
        ):
            raise ValueError(f"Rollout group has duplicate or excess samples: {path}")
        if len({sample.prompt for sample in samples}) != 1:
            raise ValueError(f"Rollout group has inconsistent prompt: {path}")
        prompt = samples[0].prompt
        question = prompt
        if "\nUser:" in prompt:
            question = prompt.partition("\nUser:")[2].partition("\n")[2]
            question = question.removesuffix("\nAssistant:")
        task_id = identity(question)
        item = trained_by_id.get(task_id)
        if item is None or item.prompt != question:
            raise ValueError(f"Rollout question is absent from official trained data: {path}")
        if item.source == "verified-synthetic":
            continue
        if item.source != "torl-train" or task_id not in pool_by_id or task_id in excluded:
            raise ValueError(f"Rollout question is absent from official trained data: {path}")
        rewards = tuple(float(sample.reward) for sample in samples)
        if not {0.0, 1.0}.issubset(rewards):
            continue
        complete = len(samples) == n_samples
        if complete:
            complete_mixed += 1
        else:
            partial_mixed += 1
        evidence.append(MixedRolloutEvidence(task_id, source, len(samples), rewards, complete))
    selected = tuple(sorted({item.task_id for item in evidence}))
    if not selected:
        raise ValueError("No within-group 0/1 mixed rollout questions were found")
    return RolloutCurriculumSelection(
        "Select a task iff one actual rollout JSONL group contains both reward 0 and reward 1; "
        "never pool rows across files.",
        frozen_pool_source,
        trained_dataset_source,
        source_refs,
        file_ref(Path(__file__)),
        len(rollout_files),
        nonempty,
        empty,
        complete_mixed,
        partial_mixed,
        selected,
        tuple(sorted(evidence, key=lambda item: (item.task_id, item.rollout_file.uri))),
    )


def normalize_gold(answer: str, gold_format: GoldFormat) -> str:
    """Make a newly written training answer parseable without changing its value."""
    answer = answer.strip()
    if gold_format == "preserve":
        return answer
    if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", answer):
        return answer
    if (
        (answer.startswith("$") and answer.endswith("$"))
        or (answer.startswith(r"\(") and answer.endswith(r"\)"))
        or (answer.startswith(r"\[") and answer.endswith(r"\]"))
    ):
        return answer
    return f"${answer}$"


def generate(rng: random.Random, family: Family, difficulty: int) -> Example:
    """Construct constrained questions; cross-check formulas using a separate oracle."""
    oracle: int | Fraction | str
    answer: int | str
    name = rng.choice(("Maya", "Noah", "Lena", "Sam", "Aria", "Ben"))
    a, b = rng.randint(3, 12 + difficulty * 9), rng.randint(2, 6 + difficulty * 3)
    c, d = rng.randint(2, 5 + difficulty * 4), rng.randint(2, 5 + difficulty)
    if family == "inventory":
        # Whole packages, no rounding ambiguity; simulate the stock changes.
        sold = rng.randint(1, a - 1)
        prompt = (
            f"{name} has {a} boxes with {b} pencils in each box. After selling {sold} full "
            f"boxes, {name} buys {c} more boxes containing {d} pencils each. "
            "How many pencils does the store now have?"
        )
        answer = (a - sold) * b + c * d
        stock = [b for _ in range(a)]
        del stock[:sold]
        stock.extend(d for _ in range(c))
        oracle = sum(stock)
    elif family == "rates":
        prompt = (
            f"{name} makes {a} bracelets per hour for {b} hours on Monday. On Tuesday, "
            f"the hourly rate increases by {c} bracelets and {name} works for {d} hours. "
            f"After giving away {c} bracelets, how many bracelets are left?"
        )
        answer = a * b + (a + c) * d - c
        oracle = sum([a] * b + [a + c] * d) - c
    elif family == "budget":
        # Prices and all quantities are integers; the budget is guaranteed sufficient.
        extra = rng.randint(5, 20 * difficulty)
        budget = a * b + c * d + extra
        prompt = (
            f"{name} has ${budget}. {name} buys {b} notebooks at ${a} each and "
            f"{d} pens at ${c} each, then earns ${a + d} by doing chores. "
            f"How many dollars does {name} have now?"
        )
        answer = budget - a * b - c * d + a + d
        oracle = budget + sum([-a] * b + [-c] * d + [a + d])
    elif family == "ratio":
        total = a * (b + c)
        prompt = (
            f"There are {total} marbles in a jar. The ratio of red marbles to blue marbles "
            f"is {b} to {c}. {name} adds {d * b} red marbles and removes {a} red marbles. "
            "How many red marbles are in the jar after these changes?"
        )
        answer = a * b + d * b - a
        red = Fraction(total * b, b + c)
        if red < a:
            raise ValueError("Invalid ratio construction")
        oracle = int(red + d * b - a)
    elif family == "growth":
        days = min(difficulty + 1, 5)
        initial = a * b
        prompt = (
            f"A game account starts with {initial} points. At the end of each day, its "
            f"points double and then {c} bonus points are added. This happens for "
            f"{days} days. {name} then spends {d} points. How many points remain?"
        )
        answer = initial * 2**days + c * (2**days - 1) - d
        oracle = initial
        for _ in range(days):
            oracle = oracle * 2 + c
        oracle -= d
    elif family == "percent":
        # Use prices that are multiples of 100 and whole-number rates so the
        # target remains an integer while exercising chained percentages.
        price = rng.randint(1, 3 + difficulty * 4) * 400
        increase = rng.choice((10, 20, 25, 30, 40))
        discount = rng.choice((10, 20, 25, 30))
        prompt = (
            f"A machine costs ${price}. Its price increases by {increase}% and then a "
            f"promotion discounts the increased price by {discount}%. "
            "What is the final price in dollars?"
        )
        answer = price * (100 + increase) * (100 - discount) // 10000
        exact_price = Fraction(price) * (100 + increase) / 100
        exact_price *= Fraction(100 - discount, 100)
        if exact_price.denominator != 1:
            raise ValueError("Percentage construction must have an exact integer result")
        oracle = exact_price.numerator
    elif family == "average":
        # Construct a list with an exact integer mean.  The final value is
        # derived from the target sum rather than rounded after generation.
        count = rng.randint(3, 4 + difficulty)
        mean = rng.randint(8, 25 + difficulty * 10)
        spread = min(3, (mean - 1) // max(1, count - 1))
        values = [mean + rng.randint(-spread, spread) for _ in range(count - 1)]
        values.append(mean * count - sum(values))
        if values[-1] < 1:
            raise ValueError("Invalid average construction")
        joined = ", ".join(str(value) for value in values)
        prompt = f"The average of these {count} scores is requested: {joined}. What is the average?"
        answer = mean
        exact_average = Fraction(sum(values), count)
        if exact_average.denominator != 1:
            raise ValueError("Average construction must have an exact integer result")
        oracle = exact_average.numerator
    elif family == "units":
        batches = rng.randint(4, 12 + difficulty * 8)
        # Keep the conversion exact and ask for the total after a multi-step
        # quantity calculation rather than teaching decimal formatting.
        units_per_batch = rng.choice((4, 6, 8, 10, 12))
        units_per_larger = rng.choice(
            tuple(divisor for divisor in (2, 4, 5) if units_per_batch % divisor == 0)
        )
        prompt = (
            f"A workshop uses {units_per_batch} cups of material for each batch and makes "
            f"{batches} batches. If one container holds {units_per_larger} cups, "
            "how many full containers of material are used?"
        )
        total = units_per_batch * batches
        if total % units_per_larger:
            raise ValueError("Invalid unit construction")
        answer = total // units_per_larger
        exact_containers = Fraction(units_per_batch * batches, units_per_larger)
        if exact_containers.denominator != 1:
            raise ValueError("Unit construction must divide into full containers")
        oracle = exact_containers.numerator
    elif family == "work":
        days = rng.randint(3, 5 + difficulty)
        hours = rng.randint(3, 8)
        hourly = rng.randint(8, 20 + difficulty * 5)
        expense = rng.choice((10, 20, 30, 40))
        prompt = (
            f"A worker earns ${hourly} per hour, works {hours} hours each day for {days} days, "
            f"and spends ${expense} on supplies. How many dollars remain from the earnings?"
        )
        answer = hourly * hours * days - expense
        earned = sum(hourly for _ in range(hours * days))
        oracle = earned - expense
    elif family == "decimal":
        # Represent decimal rates as tenths and make the quantity a multiple
        # of ten so the intended answer is exact without binary float math.
        rate_tenths = rng.randint(5, 30 + difficulty * 10)
        quantity = rng.randint(2, 8 + difficulty * 4) * 10
        bonus = rng.choice((10, 20, 30, 40))
        rate = Fraction(rate_tenths, 10)
        prompt = (
            f"A courier earns ${rate_tenths // 10}.{rate_tenths % 10} per package and "
            f"delivers {quantity} packages. A customer adds a ${bonus} tip. "
            "How many dollars does the courier receive altogether?"
        )
        exact_total = rate * quantity + Fraction(bonus)
        if exact_total.denominator != 1:
            raise ValueError("Decimal construction must have an exact integer result")
        answer = rate_tenths * quantity // 10 + bonus
        exact_oracle = sum((rate for _ in range(quantity)), Fraction(bonus))
        if exact_oracle.denominator != 1:
            raise ValueError("Decimal oracle must be an exact integer")
        oracle = exact_oracle.numerator
    elif family == "fraction":
        denominator = rng.randint(3, 7 + difficulty)
        numerator = rng.randint(1, denominator - 1)
        whole = rng.randint(1, 5 + difficulty)
        fraction_value = Fraction(whole * denominator + numerator, denominator)
        prompt = (
            f"A recipe needs {whole} and {numerator}/{denominator} cups of flour. "
            f"How many cups of flour are needed in total? Give the exact answer."
        )
        answer = rf"{fraction_value.numerator}/{fraction_value.denominator}"
        # Recompute the target from the two quantities in the prompt.  This
        # deliberately does not reuse ``value`` or the answer formatter.
        oracle = Fraction(whole) + Fraction(numerator, denominator)
    elif family == "radical":
        leg = rng.randint(2, 5 + difficulty)
        other = rng.randint(2, 5 + difficulty)
        square_sum = leg * leg + other * other
        square_factor = 1
        squarefree = 1
        remainder = square_sum
        divisor = 2
        while divisor * divisor <= remainder:
            power = 0
            while remainder % divisor == 0:
                remainder //= divisor
                power += 1
            square_factor *= divisor ** (power // 2)
            if power % 2:
                squarefree *= divisor
            divisor += 1
        if remainder > 1:
            squarefree *= remainder
        if squarefree == 1:
            radical = str(square_factor)
        elif square_factor == 1:
            radical = rf"\sqrt{{{squarefree}}}"
        else:
            radical = rf"{square_factor}\sqrt{{{squarefree}}}"
        prompt = (
            f"A right triangle has perpendicular sides of lengths {leg} and {other}. "
            "What is the exact length of its hypotenuse?"
        )
        answer = radical
        # Verify the rendered answer against the two side lengths in the
        # prompt; comparing it with ``radical`` would only test formatting.
        oracle = square_sum
    elif family == "linear":
        x = rng.randint(2, 12 + difficulty * 3)
        coefficient = rng.randint(2, 6 + difficulty)
        offset = rng.randint(1, 12)
        total = coefficient * x + offset
        prompt = (
            f"A number x satisfies {coefficient}x + {offset} = {total}. What is the value of x?"
        )
        answer = str(x)
        oracle = Fraction(total - offset, coefficient)
    elif family == "counting":
        people = rng.randint(5, 8 + difficulty)
        choose = rng.randint(2, min(4, people - 1))
        count = 1
        for factor in range(people - choose + 1, people + 1):
            count *= factor
        for factor in range(2, choose + 1):
            count //= factor
        prompt = (
            f"A committee of {choose} different people is chosen from {people} volunteers. "
            "How many different committees are possible?"
        )
        answer = str(count)
        oracle = sum(1 for _ in combinations(range(people), choose))
    elif family == "sequence":
        first = rng.randint(2, 12 + difficulty)
        difference = rng.randint(2, 5 + difficulty)
        term = rng.randint(4, 8 + difficulty)
        term_value = first + (term - 1) * difference
        prompt = (
            f"An arithmetic sequence starts with {first} and has common difference "
            f"{difference}. What is its {term}th term?"
        )
        answer = str(term_value)
        oracle = first
        for _ in range(term - 1):
            oracle += difference
    answer_text = str(answer)
    if family == "fraction":
        valid = Fraction(answer_text) == oracle
    elif family == "radical":
        complete = re.fullmatch(r"(\d+)", answer_text)
        radical_match = re.fullmatch(r"(?:(\d+)\\sqrt\{(\d+)\}|\\sqrt\{(\d+)\})", answer_text)
        if complete:
            rendered_square = int(complete.group(1)) ** 2
        elif radical_match:
            coefficient = int(radical_match.group(1) or 1)
            radicand = int(radical_match.group(2) or radical_match.group(3))
            rendered_square = coefficient * coefficient * radicand
        else:
            rendered_square = -1
        valid = rendered_square == oracle
    else:
        valid = Fraction(answer_text) == oracle and Fraction(answer_text) >= 0
    if not valid:
        raise ValueError("Synthetic answer failed independent verification")
    return Example(
        identity(prompt),
        prompt,
        str(answer),
        "verified-synthetic",
        family=family,
        difficulty=difficulty,
    )


def build_dataset(
    recipe: DataRecipe,
    pool: tuple[Example, ...],
    pool_ref: ContentRef,
    excluded_ids: tuple[str, ...],
    output: Path,
) -> SynthesisManifest:
    if any(item.source != "torl-train" for item in pool):
        raise ValueError("Only official training examples may enter synthesis")
    excluded = set(excluded_ids)
    if excluded.intersection(item.task_id for item in pool):
        raise ValueError("Held-out example in the training pool")
    focused = set(recipe.real_task_ids)
    if recipe.curriculum is not None:
        curriculum = read_record(local_path(recipe.curriculum), DynamicCurriculum)
        if curriculum.training_pool != pool_ref:
            raise ValueError("Dynamic curriculum belongs to a different frozen training pool")
        # Revalidate the actual model samples, including their aggregate report.
        groups = probe_groups(read_record(local_path(curriculum.probe), StudentProbe))
        mixed = set(curriculum.mixed_task_ids)
        random_ids = set(curriculum.random_task_ids)
        if (
            not mixed
            or not random_ids
            or mixed & random_ids
            or len(mixed) != len(curriculum.mixed_task_ids)
            or len(random_ids) != len(curriculum.random_task_ids)
            or not mixed <= {group.task_id for group in groups if group.mixed}
        ):
            raise ValueError("Dynamic curriculum must retain mixed evidence and random coverage")
        focused = set((*curriculum.mixed_task_ids, *curriculum.random_task_ids))
        if len(focused) != recipe.real_count:
            raise ValueError("Dynamic curriculum size differs from real_count")
    if focused.intersection(excluded):
        raise ValueError("Focused training task is held out")
    available = pool
    if focused:
        pool_ids = {item.task_id for item in pool}
        unknown = focused - pool_ids
        if unknown:
            raise ValueError("Focused training task ID is absent from the official pool")
        available = tuple(item for item in available if item.task_id in focused)
    if recipe.real_count > len(available):
        raise ValueError("Requested real examples exceed the eligible training pool")
    rng = random.Random(recipe.seed)
    items = [
        replace(item, answer=normalize_gold(item.answer, recipe.gold_format))
        for item in rng.sample(available, recipe.real_count)
    ]
    seen = {item.task_id for item in (*pool,)} | excluded
    if recipe.synthetic_admission is not None:
        admission = read_record(local_path(recipe.synthetic_admission), SyntheticAdmission)
        candidates = _compositional_candidates(
            admission.proposals, admission.probe, admission.policy, excluded_ids
        )
        if not set(admission.selected_ids) <= set(candidates.eligible_ids):
            raise ValueError("Admitted synthetic tasks lack mixed correctness or tool evidence")
        definitions = {item.task_id: item for item in candidates.examples}
        admitted = read_examples(local_path(admission.dataset))
        families = {item.family for item in candidates.examples}
        if any(
            sum(item.family == family for item in admitted) < admission.policy.minimum_per_family
            for family in families
        ):
            raise ValueError("Admitted synthetic dataset does not satisfy its family minimum")
        if (
            len(admitted) != recipe.synthetic_count
            or tuple(item.task_id for item in admitted) != admission.selected_ids
            or any(item.source != "verified-synthetic" or item.task_id in seen for item in admitted)
            or any(definitions.get(item.task_id) != item for item in admitted)
        ):
            raise ValueError("Admitted synthetic dataset is inconsistent or overlaps real data")
        items.extend(
            replace(item, answer=normalize_gold(item.answer, recipe.gold_format))
            for item in admitted
        )
    # Bound retries so a broken/degenerate research generator cannot hang a stage.
    for _ in range(max(1000, recipe.synthetic_count * 20)):
        if len(items) == recipe.real_count + recipe.synthetic_count:
            break
        family = rng.choices(
            [item.family for item in recipe.synthesis.families],
            [item.weight for item in recipe.synthesis.families],
        )[0]
        difficulties = range(recipe.synthesis.min_difficulty, recipe.synthesis.max_difficulty + 1)
        weights = recipe.synthesis.difficulty_weights or (1,) * len(difficulties)
        difficulty = rng.choices(tuple(difficulties), weights)[0]
        item = generate(rng, family, difficulty)
        if item.task_id not in seen:
            seen.add(item.task_id)
            items.append(replace(item, answer=normalize_gold(item.answer, recipe.gold_format)))
    if len(items) != recipe.real_count + recipe.synthetic_count:
        raise ValueError("Synthesis exhausted its unique-question retry limit")
    rng.shuffle(items)
    output.mkdir(parents=True, exist_ok=False)
    ref = write_examples(output / "train.jsonl", tuple(items))
    manifest = SynthesisManifest(recipe, pool_ref, ref, recipe.real_count, recipe.synthetic_count)
    write_record(output / "manifest.json", manifest)
    return manifest
