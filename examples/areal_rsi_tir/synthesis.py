"""Audited, deterministic math synthesis for the TIR training pipeline."""

from __future__ import annotations

import hashlib
import json
import os
import random
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from math import gcd
from pathlib import Path

from areal_rsi.api.control import json_bytes
from areal_rsi.api.records import RsiSplit, TaskPartition, TaskSetVersion
from areal_rsi.infra.storage.files import write_bytes
from areal_rsi_tir.tasks import (
    TirCase,
    _identity,
    _partition,
    _regular_bytes,
    _sha256,
    load_tir_task_set,
)


class SyntheticMathFamily(StrEnum):
    TICKETS = "tickets"
    RATIO = "ratio"
    DISCOUNT = "discount"
    RECTANGLE = "rectangle"
    SEQUENCE = "sequence"
    MACHINES = "machines"
    REMAINING_FRACTION = "remaining_fraction"
    LINEAR = "linear"


@dataclass(frozen=True, slots=True)
class SyntheticMathCase:
    family: SyntheticMathFamily
    case: TirCase


@dataclass(frozen=True, slots=True)
class SyntheticSelection:
    cases: tuple[SyntheticMathCase, ...]
    zero_passes: int
    one_pass: int
    eligible_cases: int


def _synthetic_problem(family: SyntheticMathFamily, rng: random.Random) -> tuple[str, str]:
    """Build a short, exact math problem with an independently computed answer."""
    match family:
        case SyntheticMathFamily.TICKETS:
            adult_count, child_count = rng.randint(8, 25), rng.randint(7, 24)
            child_price = rng.randint(5, 14)
            adult_price = child_price + rng.randint(5, 18)
            total = adult_count * adult_price + child_count * child_price
            return (
                f"A museum sold {adult_count + child_count} tickets. Adult tickets cost "
                f"${adult_price} each and child tickets cost ${child_price} each. "
                f"The total revenue was ${total}. How many adult tickets were sold?",
                str(adult_count),
            )
        case SyntheticMathFamily.RATIO:
            first, second = rng.randint(2, 9), rng.randint(2, 9)
            while first == second or gcd(first, second) != 1:
                second = rng.randint(2, 9)
            scale = rng.randint(4, 45)
            return (
                f"The numbers of red and blue tiles are in the ratio {first}:{second}. "
                f"There are {scale * (first + second)} tiles altogether. "
                "How many blue tiles are there?",
                str(scale * second),
            )
        case SyntheticMathFamily.DISCOUNT:
            price = 20 * rng.randint(8, 60)
            discount = 5 * rng.randint(1, 6)
            shipping = rng.randint(3, 19)
            final = price * (100 - discount) // 100 + shipping
            return (
                f"A jacket costs ${price}. A {discount}% discount is applied, then "
                f"${shipping} is added for shipping. What is the final amount paid in dollars?",
                str(final),
            )
        case SyntheticMathFamily.RECTANGLE:
            width = rng.randint(6, 45)
            length = width + rng.randint(2, 30)
            perimeter = 2 * (width + length)
            return (
                f"A rectangular garden has perimeter {perimeter} meters. Its length "
                f"is {length - width} meters more than its width. "
                "What is its area in square meters?",
                str(length * width),
            )
        case SyntheticMathFamily.SEQUENCE:
            first = rng.randint(1, 14)
            difference = rng.randint(2, 8)
            count = rng.randint(9, 20)
            third, seventh = first + 2 * difference, first + 6 * difference
            total = count * (2 * first + (count - 1) * difference) // 2
            return (
                f"In an arithmetic sequence, the third term is {third} and the "
                f"seventh term is {seventh}. What is the sum of the first {count} terms?",
                str(total),
            )
        case SyntheticMathFamily.MACHINES:
            first_machines, first_hours = rng.randint(2, 5), rng.randint(2, 5)
            hourly_rate = rng.randint(3, 12)
            second_machines, second_hours = rng.randint(4, 8), rng.randint(3, 7)
            return (
                f"Working at the same constant rate, {first_machines} machines make "
                f"{first_machines * first_hours * hourly_rate} parts in {first_hours} "
                f"hours. How many parts will {second_machines} machines make in "
                f"{second_hours} hours?",
                str(second_machines * second_hours * hourly_rate),
            )
        case SyntheticMathFamily.REMAINING_FRACTION:
            first_denominator, second_denominator = rng.randint(3, 12), rng.randint(3, 12)
            first_numerator = rng.randint(1, first_denominator - 1)
            second_numerator = rng.randint(1, second_denominator - 1)
            remaining = (1 - Fraction(first_numerator, first_denominator)) * (
                1 - Fraction(second_numerator, second_denominator)
            )
            return (
                f"A container starts full. First {first_numerator}/{first_denominator} "
                f"of its contents are used, then {second_numerator}/{second_denominator} "
                "of what remains is used. What fraction of the original contents remains?",
                str(remaining),
            )
        case SyntheticMathFamily.LINEAR:
            answer = rng.randint(4, 35)
            left_factor = rng.randint(3, 8)
            right_factor = rng.randint(1, left_factor - 1)
            left_addend = rng.randint(3, 19)
            right_addend = (left_factor - right_factor) * answer + left_addend
            # Preserve the RNG stream of the other seven families across this revision.
            local = random.Random(f"{answer}:{left_factor}:{right_factor}:{left_addend}")
            denominator = local.randint(2, 9)
            other_denominator = local.randint(2, 9)
            if left_factor * other_denominator == right_factor * denominator:
                other_denominator += 10
            solution = Fraction(answer, local.randint(2, 7))
            rhs = Fraction(left_factor * solution + left_addend, denominator) - Fraction(
                right_factor * solution + right_addend, other_denominator
            )
            return (
                f"Solve for x: ({left_factor}*x + {left_addend})/{denominator} - "
                f"({right_factor}*x + {right_addend})/{other_denominator} = {rhs}. "
                "Give the exact value of x as an integer or a reduced fraction.",
                str(solution),
            )

    raise ValueError(f"Unknown synthetic math family: {family}")


def synthesize_tir_math_cases(*, seed: int, count: int) -> tuple[SyntheticMathCase, ...]:
    """Sample balanced, parameterized problems sized for a 512-token solver budget."""
    if type(seed) is not int or seed < 0 or type(count) is not int or count < 8 or count % 8:
        raise ValueError("Synthetic math needs a nonnegative seed and a count divisible by eight")
    rng = random.Random(seed)
    families = tuple(SyntheticMathFamily)
    cases: list[SyntheticMathCase] = []
    seen: set[str] = set()
    for index in range(count):
        family = families[index % len(families)]
        for _ in range(100):
            question, answer = _synthetic_problem(family, rng)
            normalized = " ".join(question.casefold().split())
            task_id = hashlib.sha256(normalized.encode()).hexdigest()
            if task_id not in seen:
                break
        else:
            raise ValueError("Synthetic math recipe exhausted its distinct parameters")
        seen.add(task_id)
        cases.append(SyntheticMathCase(family, TirCase(task_id, question, answer)))
    return tuple(cases)


def synthesize_tir_supplement(*, seed: int, count: int = 256) -> tuple[SyntheticMathCase, ...]:
    """Add two-stage ratio and production problems after easy-family calibration."""
    if type(seed) is not int or seed < 0 or type(count) is not int or count < 2 or count % 2:
        raise ValueError("Supplement needs a nonnegative seed and a positive even count")
    rng = random.Random(seed)
    cases: list[SyntheticMathCase] = []
    seen: set[str] = set()
    while len(cases) < count:
        if len(cases) % 2 == 0:
            family = SyntheticMathFamily.RATIO
            red, blue, scale = rng.randint(2, 11), rng.randint(2, 11), rng.randint(7, 43)
            addition = rng.randint(5, 60)
            after = Fraction(red * scale + addition, blue * scale)
            question = (
                f"Initially red and blue beads are in the ratio {red}:{blue}. "
                f"After adding {addition} red beads and no blue beads, the ratio is "
                f"{after.numerator}:{after.denominator}. How many beads were there initially?"
            )
            answer = str((red + blue) * scale)
        else:
            family = SyntheticMathFamily.MACHINES
            machines, hours, rate = rng.randint(2, 7), rng.randint(2, 8), rng.randint(3, 17)
            active, first_hours = rng.randint(4, 11), rng.randint(2, 7)
            stopped, second_hours = rng.randint(1, active - 1), rng.randint(2, 9)
            factor = Fraction(rng.randint(2, 7), rng.randint(2, 7))
            question = (
                f"{machines} identical machines produce {machines * hours * rate} parts in "
                f"{hours} hours at a constant rate. A new shift starts with {active} machines "
                f"working at that rate for {first_hours} hours. Then {stopped} machines stop; "
                f"each remaining machine works at {factor} times its original rate for "
                f"{second_hours} more hours. How many parts are produced during the new shift? "
                "Give an exact integer or reduced fraction."
            )
            answer = str(
                active * first_hours * rate + (active - stopped) * second_hours * rate * factor
            )
        task_id = hashlib.sha256(" ".join(question.casefold().split()).encode()).hexdigest()
        if task_id in seen:
            continue
        seen.add(task_id)
        cases.append(SyntheticMathCase(family, TirCase(task_id, question, answer)))
    return tuple(cases)


def select_model_calibrated_cases(
    candidates: tuple[SyntheticMathCase, ...],
    successes: tuple[int | None, ...],
    *,
    count: int,
    seed: int,
) -> SyntheticSelection:
    """Prefer borderline cases; exclude incomplete probes and twice-solved cases."""
    if (
        len(candidates) != len(successes)
        or type(count) is not int
        or count < 8
        or count % 8
        or type(seed) is not int
        or seed < 0
        or any(
            value is not None and (type(value) is not int or value not in (0, 1, 2))
            for value in successes
        )
    ):
        raise ValueError("Invalid synthetic difficulty probe inputs")
    eligible = tuple(
        (item, value)
        for item, value in zip(candidates, successes, strict=True)
        if value is not None and value < 2
    )
    if len(eligible) < count:
        raise ValueError("Too few model-calibrated synthetic cases; expand or revise the pool")

    def rank(pair: tuple[SyntheticMathCase, int]) -> tuple[int, bytes]:
        item, value = pair
        return (
            0 if value == 1 else 1,
            hashlib.sha256(f"{seed}:{item.case.task_id}".encode()).digest(),
        )

    minimum_per_family = count // 16
    chosen: list[tuple[SyntheticMathCase, int]] = []
    for family in SyntheticMathFamily:
        group = sorted((pair for pair in eligible if pair[0].family is family), key=rank)
        if len(group) < minimum_per_family:
            raise ValueError(f"Too few model-calibrated cases in family {family.value}")
        chosen.extend(group[:minimum_per_family])
    chosen_ids = {item.case.task_id for item, _ in chosen}
    remaining = sorted(
        (pair for pair in eligible if pair[0].case.task_id not in chosen_ids), key=rank
    )
    chosen.extend(remaining[: count - len(chosen)])
    one_pass = sum(value == 1 for _, value in chosen)
    if one_pass < count // 4:
        raise ValueError("Too few borderline cases; current generator misses model difficulty")
    chosen.sort(
        key=lambda pair: hashlib.sha256(f"train:{seed}:{pair[0].case.task_id}".encode()).digest()
    )
    return SyntheticSelection(
        tuple(item for item, _ in chosen), count - one_pass, one_pass, len(eligible)
    )


def _source_bytes(cases: tuple[SyntheticMathCase, ...]) -> bytes:
    return b"".join(
        json_bytes(
            {
                "family": item.family.value,
                "task_id": item.case.task_id,
                "messages": [{"role": "user", "content": item.case.question}],
                "answer": item.case.answer,
            }
        )
        + b"\n"
        for item in cases
    )


def _check_exclusions(cases: tuple[SyntheticMathCase, ...], roots: tuple[Path, ...]) -> None:
    excluded = {
        task_id
        for root in roots
        for split in RsiSplit
        for task_id in getattr(load_tir_task_set(root), split.value).task_ids
    }
    if any(item.case.task_id in excluded for item in cases):
        raise ValueError("Synthetic question overlaps a frozen task set")


def _freeze_derived(
    parent_root: Path,
    destination: Path,
    cases: tuple[SyntheticMathCase, ...],
    *,
    source_train_digest: str,
    source_name: str,
    provenance_name: str,
    provenance: bytes,
    derivation: bytes | None = None,
) -> TaskSetVersion:
    parent = load_tir_task_set(parent_root)
    if destination.exists():
        raise FileExistsError(destination)
    source = _source_bytes(cases)
    parent_answers: dict[str, str] = json.loads(
        _regular_bytes(parent_root / "private" / "answers.json")
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".tir-synthetic-") as temporary:
        staged = Path(temporary) / "taskset"
        public, private = staged / "public", staged / "private"
        public.mkdir(parents=True)
        private.mkdir(mode=0o700)
        partitions: dict[RsiSplit, TaskPartition] = {}
        for split in RsiSplit:
            path = public / f"{split.value}.jsonl"
            if split in (RsiSplit.HARNESS_EVOLVE, RsiSplit.HARNESS_SELECT, RsiSplit.MODEL_TRAIN):
                partitions[split] = _partition(tuple(item.case for item in cases), path)
            else:
                write_bytes(
                    path,
                    _regular_bytes(parent_root / "public" / f"{split.value}.jsonl"),
                    immutable=True,
                )
                partitions[split] = getattr(parent, split.value)
        answer_ids = {task_id for split in RsiSplit for task_id in partitions[split].task_ids}
        answers = {
            task_id: value for task_id, value in parent_answers.items() if task_id in answer_ids
        }
        answers.update({item.case.task_id: item.case.answer for item in cases})
        if set(answers) != answer_ids:
            raise ValueError("Derived answers do not match the public tasks")
        answer_bytes = json_bytes(answers)
        for name, content in (("answers.json", answer_bytes), (source_name, source)):
            path = private / name
            write_bytes(path, content, immutable=True)
            os.chmod(path, 0o600)
        if derivation is not None:
            path = private / "train-derivation.json"
            write_bytes(path, derivation, immutable=True)
            os.chmod(path, 0o600)
        provisional = TaskSetVersion(
            "taskset_pending",
            source_train_digest,
            parent.source_test_digest,
            _sha256(answer_bytes),
            partitions[RsiSplit.HARNESS_EVOLVE],
            partitions[RsiSplit.HARNESS_SELECT],
            partitions[RsiSplit.MODEL_TRAIN],
            partitions[RsiSplit.MODEL_DEV],
            partitions[RsiSplit.SEALED_TEST],
        )
        version = TaskSetVersion(
            _identity(provisional),
            provisional.source_train_digest,
            provisional.source_test_digest,
            provisional.answers_digest,
            provisional.harness_evolve,
            provisional.harness_select,
            provisional.model_train,
            provisional.model_dev,
            provisional.sealed_test,
        )
        write_bytes(staged / "manifest.json", json_bytes(version), immutable=True)
        write_bytes(staged / provenance_name, provenance, immutable=True)
        staged.rename(destination)
    return load_tir_task_set(destination)


def freeze_tir_synthetic_probe(
    parent_root: Path,
    destination: Path,
    *,
    seed: int,
    count: int,
    excluded_task_set_roots: tuple[Path, ...],
    candidates: tuple[SyntheticMathCase, ...] | None = None,
) -> TaskSetVersion:
    """Freeze public synthetic candidates for blind retained-model difficulty probes."""
    cases = (
        candidates if candidates is not None else synthesize_tir_math_cases(seed=seed, count=count)
    )
    if len(cases) != count or len({item.case.task_id for item in cases}) != count:
        raise ValueError("Probe candidates must have the requested number of unique tasks")
    _check_exclusions(cases, (parent_root, *excluded_task_set_roots))
    source = _source_bytes(cases)
    provenance = json_bytes(
        {
            "purpose": "retained_model_difficulty_probe_only",
            "parent_task_set_id": load_tir_task_set(parent_root).task_set_id,
            "seed": seed,
            "candidate_count": count,
            "candidate_source_digest": _sha256(source),
            "generator_source_digest": _sha256(_regular_bytes(Path(__file__))),
        }
    )
    return _freeze_derived(
        parent_root,
        destination,
        cases,
        source_train_digest=_sha256(source),
        source_name="probe-source.jsonl",
        provenance_name="probe-provenance.json",
        provenance=provenance,
    )


def freeze_tir_synthetic_training(
    parent_root: Path,
    destination: Path,
    selected: tuple[SyntheticMathCase, ...],
    *,
    seed: int,
    probe_task_set_ids: tuple[str, ...],
    probe_report_digests: tuple[str, ...],
    selected_pass_counts: tuple[int, int],
    excluded_task_set_roots: tuple[Path, ...],
) -> TaskSetVersion:
    """Freeze a synthetic-only training split selected by blind model probes."""
    parent = load_tir_task_set(parent_root)
    if len(selected) != len(parent.model_train.task_ids):
        raise ValueError("Synthetic-only arm must match the original training case count")
    if len({item.case.task_id for item in selected}) != len(selected):
        raise ValueError("Synthetic training cases must be distinct")
    if sum(selected_pass_counts) != len(selected):
        raise ValueError("Every selected case needs a recorded probe pass count")
    _check_exclusions(selected, (parent_root, *excluded_task_set_roots))
    source = _source_bytes(selected)
    provenance = json_bytes(
        {
            "parent_task_set_id": parent.task_set_id,
            "seed": seed,
            "original_train_cases": 0,
            "synthetic_train_cases": len(selected),
            "synthetic_source_digest": _sha256(source),
            "generator_source_digest": _sha256(_regular_bytes(Path(__file__))),
            "probe_task_set_ids": list(probe_task_set_ids),
            "probe_report_digests": probe_report_digests,
            "selected_pass_counts_zero_one": selected_pass_counts,
            "families": {
                family.value: sum(item.family is family for item in selected)
                for family in SyntheticMathFamily
            },
        }
    )
    derivation = json_bytes(
        {
            "parent_task_set_id": parent.task_set_id,
            "parent_source_train_digest": parent.source_train_digest,
            "synthetic_source_digest": _sha256(source),
            "provenance_digest": _sha256(provenance),
        }
    )
    return _freeze_derived(
        parent_root,
        destination,
        selected,
        source_train_digest=_sha256(derivation),
        source_name="synthetic-source.jsonl",
        provenance_name="synthetic-provenance.json",
        provenance=provenance,
        derivation=derivation,
    )
