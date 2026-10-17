"""TIR-specific, deterministically verifiable synthetic integer math problems."""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from areal_rsi.api.control import decode_record, encode_record
from areal_rsi.api.records import ContentRef
from areal_rsi.infra.artifacts import file_ref, write_record


@dataclass(frozen=True, slots=True)
class SynthesisConfig:
    seed: int = 17
    train_size: int = 32
    valid_size: int = 8
    test_size: int = 8

    def __post_init__(self) -> None:
        if min(self.train_size, self.valid_size, self.test_size) < 1:
            raise ValueError("All dataset splits must be nonempty")
        if self.train_size + self.valid_size + self.test_size > 100_000:
            raise ValueError("This example supports at most 100000 synthetic problems")


@dataclass(frozen=True, slots=True)
class MathProblem:
    task_id: str
    prompt: str
    answer: str
    family: Literal["arithmetic", "sum_squares", "modular_count"]
    a: int
    b: int
    c: int


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    synthesis: SynthesisConfig
    train: ContentRef
    valid: ContentRef
    test: ContentRef


def problem(
    family: Literal["arithmetic", "sum_squares", "modular_count"], a: int, b: int, c: int
) -> MathProblem:
    if family == "arithmetic":
        prompt = f"Compute {a} * {b} + {c}."
        answer = a * b + c
    elif family == "sum_squares":
        prompt = f"Compute the sum of i^2 + {a}*i + {b} for integers i from 1 through {c}."
        answer = c * (c + 1) * (2 * c + 1) // 6 + a * c * (c + 1) // 2 + b * c
    else:
        prompt = f"How many integers x from 1 through {c} satisfy ({a}*x + {b}) modulo 7 = 0?"
        # Count whole periods and then the remainder, independently of validation.
        period = sum((a * x + b) % 7 == 0 for x in range(1, 8))
        answer = c // 7 * period + sum((a * x + b) % 7 == 0 for x in range(1, c % 7 + 1))
    task_id = hashlib.sha256(prompt.encode()).hexdigest()[:24]
    return MathProblem(task_id, prompt, str(answer), family, a, b, c)


def validate_problem(item: MathProblem) -> None:
    if not (2 <= item.a <= 99 and 1 <= item.b <= 99 and 20 <= item.c <= 300):
        raise ValueError("Problem parameters are outside the synthetic domain")
    if item.family == "arithmetic":
        answer = sum(item.a for _ in range(item.b)) + item.c
    elif item.family == "sum_squares":
        answer = sum(i * i + item.a * i + item.b for i in range(1, item.c + 1))
    else:
        answer = sum((item.a * x + item.b) % 7 == 0 for x in range(1, item.c + 1))
    canonical = problem(item.family, item.a, item.b, item.c)
    if item != canonical or item.answer != str(answer):
        raise ValueError(f"Invalid synthetic problem or answer: {item.task_id}")


def read_problems(path: Path) -> tuple[MathProblem, ...]:
    with path.open() as stream:
        items = tuple(decode_record(MathProblem, json.loads(line)) for line in stream)
    for item in items:
        validate_problem(item)
    if not items or len({item.task_id for item in items}) != len(items):
        raise ValueError("Dataset is empty or contains duplicate problems")
    return items


def synthesize(config: SynthesisConfig, output: Path) -> DatasetManifest:
    output.mkdir(parents=True, exist_ok=False)
    families: tuple[Literal["arithmetic", "sum_squares", "modular_count"], ...] = (
        "arithmetic",
        "sum_squares",
        "modular_count",
    )
    seen: set[str] = set()

    def generate(name: str, count: int) -> list[MathProblem]:
        rng = random.Random(f"{config.seed}:{name}")
        split: list[MathProblem] = []
        while len(split) < count:
            item = problem(
                rng.choice(families), rng.randint(2, 99), rng.randint(1, 99), rng.randint(20, 300)
            )
            validate_problem(item)
            if item.task_id not in seen:
                seen.add(item.task_id)
                split.append(item)
        return split

    # Freeze test before synthesizing training examples: increasing the
    # training budget must not silently change the comparison benchmark.
    # Retain seed-stream keys so split renaming preserves generated questions.
    test = generate("eval", config.test_size)
    valid = generate("dev", config.valid_size)
    train = generate("train", config.train_size)
    splits = (train, valid, test)
    refs = []
    for name, split in zip(("train", "valid", "test"), splits, strict=True):
        path = output / f"{name}.jsonl"
        with path.open("x") as stream:
            for item in split:
                stream.write(json.dumps(encode_record(item), ensure_ascii=False) + "\n")
        refs.append(file_ref(path))
    manifest = DatasetManifest(config, *refs)
    write_record(output / "manifest.json", manifest)
    return manifest
