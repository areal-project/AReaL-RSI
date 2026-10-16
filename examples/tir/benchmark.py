"""Frozen GSM8K splits and the data boundary shared by TIR study workers."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from areal_rsi.api.control import decode_record, encode_record
from areal_rsi.api.records import ContentRef
from areal_rsi.infra.artifacts import file_ref, write_record


def identity(prompt: str) -> str:
    normalized = " ".join(prompt.casefold().split())
    return hashlib.sha256(normalized.encode()).hexdigest()[:24]


@dataclass(frozen=True, slots=True)
class Example:
    task_id: str
    prompt: str
    answer: str
    source: Literal["gsm8k-train", "gsm8k-test", "verified-synthetic"]
    reference_solution: str = ""
    family: str = "gsm8k"
    difficulty: int = 0
    parent_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.prompt.strip() or self.task_id != identity(self.prompt):
            raise ValueError("Example identity must match its normalized question")
        if not re.fullmatch(r"-?\d+", self.answer):
            raise ValueError("This TIR study requires an exact integer ground truth")
        if self.difficulty < 0:
            raise ValueError("Difficulty must be nonnegative")


@dataclass(frozen=True, slots=True)
class Benchmark:
    seed: int
    official_train: ContentRef
    official_test: ContentRef
    train: ContentRef
    dev: ContentRef
    final: ContentRef
    # Store identities only; synthesis never needs held-out questions/solutions.
    excluded_ids: tuple[str, ...]


def read_examples(path: Path) -> tuple[Example, ...]:
    with path.open() as stream:
        items = tuple(decode_record(Example, json.loads(line)) for line in stream)
    if not items or len({item.task_id for item in items}) != len(items):
        raise ValueError("Dataset must be nonempty with unique normalized questions")
    return items


def write_examples(path: Path, items: tuple[Example, ...]) -> ContentRef:
    if not items or len({item.task_id for item in items}) != len(items):
        raise ValueError("Dataset must be nonempty with unique normalized questions")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        for item in items:
            stream.write(json.dumps(encode_record(item), ensure_ascii=False) + "\n")
    return file_ref(path)


def prepare_benchmark(
    source: Path, output: Path, *, seed: int = 20261017, dev_size: int = 256, final_size: int = 512
) -> Benchmark:
    # Optional dependency belongs to dataset preparation in the AReaL environment.
    import pyarrow.parquet as parquet  # type: ignore[import-not-found]

    if dev_size < 1 or final_size < 1:
        raise ValueError("Held-out split sizes must be positive")
    output.mkdir(parents=True, exist_ok=False)

    def load(split: Literal["train", "test"]) -> tuple[Path, tuple[Example, ...]]:
        path = source / f"{split}-00000-of-00001.parquet"
        rows = parquet.read_table(path, columns=["question", "answer"]).to_pylist()
        items = []
        for row in rows:
            solution, separator, answer = row["answer"].rpartition("####")
            if not separator:
                raise ValueError("GSM8K answer is missing the official final-answer separator")
            prompt = row["question"].strip()
            items.append(
                Example(
                    identity(prompt),
                    prompt,
                    answer.strip().replace(",", ""),
                    "gsm8k-train" if split == "train" else "gsm8k-test",
                    solution.strip(),
                )
            )
        if len({item.task_id for item in items}) != len(items):
            raise ValueError("Duplicate normalized questions in official data")
        return path, tuple(items)

    train_path, train = load("train")
    test_path, test = load("test")
    if dev_size >= len(train) or final_size > len(test):
        raise ValueError("Requested held-out data exceeds the official split")
    train_order = list(train)
    test_order = list(test)
    random.Random(f"{seed}:dev").shuffle(train_order)
    random.Random(f"{seed}:final").shuffle(test_order)
    dev = tuple(train_order[:dev_size])
    final = tuple(test_order[:final_size])
    # Exclude *all* official test questions, including unused ones, from training.
    excluded = {item.task_id for item in (*dev, *test)}
    training = tuple(item for item in train_order[dev_size:] if item.task_id not in excluded)
    benchmark = Benchmark(
        seed,
        file_ref(train_path),
        file_ref(test_path),
        write_examples(output / "train.jsonl", training),
        write_examples(output / "dev.jsonl", dev),
        write_examples(output / "heldout" / "final.jsonl", final),
        tuple(sorted(excluded)),
    )
    write_record(output / "benchmark.json", benchmark)
    return benchmark


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20261017)
    parser.add_argument("--dev-size", type=int, default=256)
    parser.add_argument("--final-size", type=int, default=512)
    args = parser.parse_args()
    result = prepare_benchmark(
        args.source,
        args.output.resolve(),
        seed=args.seed,
        dev_size=args.dev_size,
        final_size=args.final_size,
    )
    print(f"benchmark={args.output.resolve() / 'benchmark.json'}")
    print(f"train_sha256={result.train.digest} dev_sha256={result.dev.digest}")
