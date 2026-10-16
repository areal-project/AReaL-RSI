"""Editable TIR research policy: verified word problems mixed with training-only GSM8K.

The Leader may change these generators and recipes between rounds. Each generated
dataset is immutable and its source archive is recorded by the study pipeline.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Literal

from areal_rsi.api.records import ContentRef
from areal_rsi.infra.artifacts import write_record
from examples.tir.benchmark import Example, identity, write_examples

Family = Literal["inventory", "rates", "budget", "ratio", "growth"]


@dataclass(frozen=True, slots=True)
class FamilyWeight:
    family: Family
    weight: int = 1

    def __post_init__(self) -> None:
        if self.weight < 1:
            raise ValueError("Family weights must be positive")


@dataclass(frozen=True, slots=True)
class SynthesisPolicy:
    families: tuple[FamilyWeight, ...] = field(
        default_factory=lambda: tuple(
            FamilyWeight(family) for family in ("inventory", "rates", "budget", "ratio", "growth")
        )
    )
    min_difficulty: int = 1
    max_difficulty: int = 3

    def __post_init__(self) -> None:
        if not self.families or len({item.family for item in self.families}) != len(self.families):
            raise ValueError("Supply distinct nonempty synthesis families")
        if not 1 <= self.min_difficulty <= self.max_difficulty <= 5:
            raise ValueError("Synthetic difficulty must be between 1 and 5")


@dataclass(frozen=True, slots=True)
class DataRecipe:
    seed: int = 17
    real_count: int = 768
    synthetic_count: int = 256
    real_min_steps: int = 0
    synthesis: SynthesisPolicy = field(default_factory=SynthesisPolicy)

    def __post_init__(self) -> None:
        if min(self.real_count, self.synthetic_count, self.real_min_steps) < 0:
            raise ValueError("Dataset counts and minimum steps must be nonnegative")
        if not 1 <= self.real_count + self.synthetic_count <= 20000:
            raise ValueError("A research round requires 1..20000 distinct training examples")


@dataclass(frozen=True, slots=True)
class SynthesisManifest:
    recipe: DataRecipe
    training_pool: ContentRef
    dataset: ContentRef
    real_count: int
    synthetic_count: int


def generate(rng: random.Random, family: Family, difficulty: int) -> Example:
    """Construct constrained questions; cross-check formulas using a separate oracle."""
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
    else:
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
    if answer != oracle or answer < 0:
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
    if any(item.source != "gsm8k-train" for item in pool):
        raise ValueError("Only official training examples may enter synthesis")
    excluded = set(excluded_ids)
    if excluded.intersection(item.task_id for item in pool):
        raise ValueError("Held-out example in the training pool")
    available = tuple(
        item for item in pool if item.reference_solution.count("<<") >= recipe.real_min_steps
    )
    if recipe.real_count > len(available):
        raise ValueError("Requested real examples exceed the eligible training pool")
    rng = random.Random(recipe.seed)
    items = rng.sample(available, recipe.real_count)
    seen = {item.task_id for item in (*pool,)} | excluded
    # Bound retries so a broken/degenerate research generator cannot hang a stage.
    for _ in range(max(1000, recipe.synthetic_count * 20)):
        if len(items) == recipe.real_count + recipe.synthetic_count:
            break
        family = rng.choices(
            [item.family for item in recipe.synthesis.families],
            [item.weight for item in recipe.synthesis.families],
        )[0]
        difficulty = rng.randint(recipe.synthesis.min_difficulty, recipe.synthesis.max_difficulty)
        item = generate(rng, family, difficulty)
        if item.task_id not in seen:
            seen.add(item.task_id)
            items.append(item)
    if len(items) != recipe.real_count + recipe.synthetic_count:
        raise ValueError("Synthesis exhausted its unique-question retry limit")
    rng.shuffle(items)
    output.mkdir(parents=True, exist_ok=False)
    ref = write_examples(output / "train.jsonl", tuple(items))
    manifest = SynthesisManifest(recipe, pool_ref, ref, recipe.real_count, recipe.synthetic_count)
    write_record(output / "manifest.json", manifest)
    return manifest
