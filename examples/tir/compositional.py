"""Composed mathematical constraints with two independent exact solution methods."""

from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import dataclass
from fractions import Fraction
from functools import cache
from itertools import combinations, pairwise, permutations
from pathlib import Path

from areal_rsi.api.control import decode_record, encode_record
from areal_rsi.api.records import ContentRef
from areal_rsi.infra.artifacts import file_ref, write_record
from examples.tir.benchmark import Example, identity, write_examples


@dataclass(frozen=True, slots=True)
class DigitConstraints:
    digits: int
    digit_sum: int
    modulus: int
    remainder: int
    max_adjacent_difference: int


@dataclass(frozen=True, slots=True)
class LatticeRoutes:
    width: int
    height: int
    checkpoint: tuple[int, int]
    blocked: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class ConditionalDraw:
    red: int
    blue: int
    green: int
    draws: int
    minimum_red: int
    minimum_blue: int
    score_modulus: int
    score_remainder: int


Definition = DigitConstraints | LatticeRoutes | ConditionalDraw


@dataclass(frozen=True, slots=True)
class CompositionalTask:
    example: Example
    definition: Definition


@dataclass(frozen=True, slots=True)
class CompositionalManifest:
    seed: int
    definitions: ContentRef
    examples: ContentRef
    count: int


def _digit_count(spec: DigitConstraints) -> int:
    @cache
    def visit(position: int, last: int, used: int, remainder: int, total: int) -> int:
        if position == spec.digits:
            return int(total == spec.digit_sum and remainder == spec.remainder)
        return sum(
            visit(
                position + 1,
                digit,
                used | (1 << digit),
                (remainder * 10 + digit) % spec.modulus,
                total + digit,
            )
            for digit in range(10)
            if not used & (1 << digit)
            and (position > 0 or digit > 0)
            and (position == 0 or abs(last - digit) <= spec.max_adjacent_difference)
            and total + digit <= spec.digit_sum
        )

    return visit(0, 0, 0, 0, 0)


def _digit_enumeration(spec: DigitConstraints) -> int:
    count = 0
    for digits in permutations(range(10), spec.digits):
        if digits[0] == 0 or sum(digits) != spec.digit_sum:
            continue
        number = int("".join(map(str, digits)))
        if number % spec.modulus != spec.remainder:
            continue
        if all(abs(a - b) <= spec.max_adjacent_difference for a, b in pairwise(digits)):
            count += 1
    return count


def _grid_dp(spec: LatticeRoutes) -> int:
    blocked = set(spec.blocked)
    states: dict[tuple[int, int, bool], int] = {(0, 0, False): 1}
    for x in range(spec.width + 1):
        for y in range(spec.height + 1):
            if (x, y) in blocked:
                continue
            for passed in (False, True):
                count = states.get((x, y, passed), 0)
                for nx, ny in ((x + 1, y), (x, y + 1)):
                    if nx <= spec.width and ny <= spec.height and (nx, ny) not in blocked:
                        key = (nx, ny, passed or (nx, ny) == spec.checkpoint)
                        states[key] = states.get(key, 0) + count
    return states.get((spec.width, spec.height, True), 0)


def _grid_inclusion_exclusion(spec: LatticeRoutes) -> int:
    def paths(start: tuple[int, int], end: tuple[int, int]) -> int:
        dx, dy = end[0] - start[0], end[1] - start[1]
        return math.comb(dx + dy, dx) if dx >= 0 and dy >= 0 else 0

    def leg(start: tuple[int, int], end: tuple[int, int]) -> int:
        obstacles = [
            point
            for point in spec.blocked
            if start[0] <= point[0] <= end[0] and start[1] <= point[1] <= end[1]
        ]
        count = 0
        for size in range(len(obstacles) + 1):
            for subset in combinations(obstacles, size):
                points = (start, *sorted(subset), end)
                ways = math.prod(paths(a, b) for a, b in pairwise(points))
                count += (-1) ** size * ways
        return count

    return leg((0, 0), spec.checkpoint) * leg(spec.checkpoint, (spec.width, spec.height))


def _draw_hypergeometric(spec: ConditionalDraw) -> Fraction:
    numerator = denominator = 0
    for red in range(spec.red + 1):
        for blue in range(spec.blue + 1):
            green = spec.draws - red - blue
            if not 0 <= green <= spec.green:
                continue
            if blue < spec.minimum_blue:
                continue
            if (2 * red + green) % spec.score_modulus != spec.score_remainder:
                continue
            ways = math.comb(spec.red, red) * math.comb(spec.blue, blue)
            ways *= math.comb(spec.green, green)
            denominator += ways
            if red >= spec.minimum_red:
                numerator += ways
    return Fraction(numerator, denominator)


def _draw_labelled_enumeration(spec: ConditionalDraw) -> Fraction:
    colors = ("r",) * spec.red + ("b",) * spec.blue + ("g",) * spec.green
    good = eligible = 0
    for selected in combinations(range(len(colors)), spec.draws):
        drawn = [colors[index] for index in selected]
        red, blue, green = drawn.count("r"), drawn.count("b"), drawn.count("g")
        if blue >= spec.minimum_blue and (
            (2 * red + green) % spec.score_modulus == spec.score_remainder
        ):
            eligible += 1
            good += red >= spec.minimum_red
    return Fraction(good, eligible)


def materialize(spec: Definition) -> CompositionalTask:
    answer: int | Fraction
    independent: int | Fraction
    if isinstance(spec, DigitConstraints):
        answer, independent = _digit_count(spec), _digit_enumeration(spec)
        prompt = (
            f"How many {spec.digits}-digit positive integers have all digits distinct, "
            f"digit sum {spec.digit_sum}, and remainder {spec.remainder} when divided by "
            f"{spec.modulus}, if the absolute difference between each pair of adjacent "
            f"digits is at most {spec.max_adjacent_difference}? A leading zero is forbidden."
        )
        family = "digit_constraints"
    elif isinstance(spec, LatticeRoutes):
        answer, independent = _grid_dp(spec), _grid_inclusion_exclusion(spec)
        prompt = (
            f"A lattice path from (0, 0) to ({spec.width}, {spec.height}) uses only steps "
            f"(1, 0) and (0, 1). It must visit {spec.checkpoint} and must never visit any "
            f"of these lattice points: {', '.join(map(str, spec.blocked))}. "
            "How many such paths are there?"
        )
        family = "lattice_routes"
    else:
        answer, independent = _draw_hypergeometric(spec), _draw_labelled_enumeration(spec)
        prompt = (
            f"An urn contains {spec.red} red, {spec.blue} blue, and {spec.green} green "
            f"distinct balls. A uniformly random subset of {spec.draws} balls is drawn "
            "without replacement. Let R, B, G be the numbers of red, blue, green balls. "
            f"Given that B >= {spec.minimum_blue} and 2R+G leaves remainder "
            f"{spec.score_remainder} modulo {spec.score_modulus}, find the exact "
            f"conditional probability that R >= {spec.minimum_red}."
        )
        family = "conditional_draws"
    if answer != independent or answer <= 0:
        raise ValueError("Independent compositional solvers disagree or task is degenerate")
    if isinstance(answer, Fraction) and answer >= 1:
        raise ValueError("Conditional task must have a nontrivial probability")
    example = Example(identity(prompt), prompt, str(answer), "verified-synthetic", family, 4)
    return CompositionalTask(example, spec)


def verify_task(task: CompositionalTask) -> None:
    if materialize(task.definition) != task:
        raise ValueError("Compositional question or gold differs from its verified definition")


def read_tasks(path: Path) -> tuple[CompositionalTask, ...]:
    with path.open() as stream:
        tasks = tuple(decode_record(CompositionalTask, json.loads(line)) for line in stream)
    if not tasks or len({item.example.task_id for item in tasks}) != len(tasks):
        raise ValueError("Compositional proposals must be distinct and nonempty")
    for task in tasks:
        verify_task(task)
    return tasks


def propose(seed: int, count: int, excluded_ids: tuple[str, ...], output: Path) -> ContentRef:
    if not 1 <= count <= 2000:
        raise ValueError("Propose 1..2000 composed tasks per student admission round")
    rng = random.Random(seed)
    seen = set(excluded_ids)
    tasks: list[CompositionalTask] = []
    for attempt in range(count * 30):
        if len(tasks) == count:
            break
        spec: Definition
        family = attempt % 3
        if family == 0:
            digits = rng.sample(range(10), rng.choice((4, 5)))
            if digits[0] == 0:
                digits.reverse()
            modulus = rng.choice((7, 11, 13, 17, 19))
            spec = DigitConstraints(
                len(digits),
                sum(digits),
                modulus,
                int("".join(map(str, digits))) % modulus,
                rng.randint(4, 8),
            )
        elif family == 1:
            width, height = rng.randint(5, 10), rng.randint(5, 10)
            checkpoint = (rng.randint(1, width - 1), rng.randint(1, height - 1))
            eligible = [
                (x, y)
                for x in range(width + 1)
                for y in range(height + 1)
                if (x, y) not in {(0, 0), (width, height), checkpoint}
            ]
            spec = LatticeRoutes(width, height, checkpoint, tuple(sorted(rng.sample(eligible, 4))))
        else:
            spec = ConditionalDraw(
                rng.randint(3, 6),
                rng.randint(3, 6),
                rng.randint(2, 5),
                rng.randint(4, 6),
                rng.randint(1, 2),
                rng.randint(1, 2),
                rng.randint(2, 4),
                rng.randint(0, 1),
            )
        try:
            task = materialize(spec)
        except (ValueError, ZeroDivisionError):
            continue
        if task.example.task_id not in seen:
            tasks.append(task)
            seen.add(task.example.task_id)
    if len(tasks) != count:
        raise ValueError("Compositional generation exhausted its bounded proposal attempts")
    output.mkdir(parents=True, exist_ok=False)
    path = output / "definitions.jsonl"
    with path.open("x") as stream:
        for task in tasks:
            stream.write(json.dumps(encode_record(task), ensure_ascii=False) + "\n")
    examples = write_examples(output / "proposals.jsonl", tuple(task.example for task in tasks))
    return write_record(
        output / "manifest.json", CompositionalManifest(seed, file_ref(path), examples, count)
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20261018)
    parser.add_argument("--count", type=int, default=256)
    args = parser.parse_args()
    propose(args.seed, args.count, (), args.output)
