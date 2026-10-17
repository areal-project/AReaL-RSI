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
]


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
        # These families target the arithmetic chains seen in dev failures
        # while remaining independent of held-out wording.
        ("percent", 2),
        ("average", 2),
        ("units", 2),
        ("work", 2),
        ("decimal", 2),
    )
    return tuple(FamilyWeight(family, weight) for family, weight in families)


@dataclass(frozen=True, slots=True)
class SynthesisPolicy:
    families: tuple[FamilyWeight, ...] = field(default_factory=_default_family_weights)
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
    oracle: int
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
    else:
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
    if any(item.source not in {"gsm8k-train", "torl-train"} for item in pool):
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
