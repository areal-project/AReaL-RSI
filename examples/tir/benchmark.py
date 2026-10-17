"""Frozen ToRL/GSM8K splits and the data boundary shared by TIR study workers."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from dataclasses import dataclass, replace
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
    source: Literal["gsm8k-train", "gsm8k-test", "torl-train", "torl-test", "verified-synthetic"]
    reference_solution: str = ""
    family: str = "gsm8k"
    difficulty: int = 0
    parent_ids: tuple[str, ...] = ()
    repetitions: int = 1

    def __post_init__(self) -> None:
        if not self.prompt.strip() or self.task_id != identity(self.prompt):
            raise ValueError("Example identity must match its normalized question")
        if not self.answer.strip():
            raise ValueError("A mathematical ground truth is required")
        if self.difficulty < 0 or self.repetitions < 1:
            raise ValueError("Difficulty must be nonnegative and repetitions positive")


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    task_id: str
    question_id: str
    prompt: str
    answer: str
    family: str


def evaluation_cases(items: tuple[Example, ...]) -> tuple[EvaluationCase, ...]:
    return tuple(
        EvaluationCase(
            item.task_id if item.repetitions == 1 else f"{item.task_id}-rep{replicate}",
            item.task_id,
            item.prompt,
            item.answer,
            item.family,
        )
        for item in items
        for replicate in range(item.repetitions)
    )


@dataclass(frozen=True, slots=True)
class ToRLMessage:
    content: str
    role: Literal["system", "user"]


@dataclass(frozen=True, slots=True)
class ToRLReward:
    ground_truth: str
    stype: str


@dataclass(frozen=True, slots=True)
class ToRLInfo:
    id: str
    split: str


@dataclass(frozen=True, slots=True)
class ToRLRow:
    data_source: str
    prompt: tuple[ToRLMessage, ...]
    ability: str
    reward_model: ToRLReward
    extra_info: ToRLInfo


def torl_examples(rows: tuple[ToRLRow, ...], *, training: bool) -> tuple[Example, ...]:
    """Collapse repeated questions, retaining official evaluation multiplicities."""
    questions: dict[str, Example] = {}
    for row in rows:
        users = [message.content for message in row.prompt if message.role == "user"]
        if len(users) != 1:
            raise ValueError("Expected one user question in a ToRL task")
        prompt = users[0].strip()
        item = Example(
            identity(prompt),
            prompt,
            row.reward_model.ground_truth.strip(),
            "torl-train" if training else "torl-test",
            family=row.data_source,
        )
        previous = questions.get(item.task_id)
        if previous is not None:
            if previous.answer != item.answer or previous.family != item.family:
                raise ValueError("Repeated ToRL question has conflicting ground truth or source")
            if not training:
                questions[item.task_id] = replace(previous, repetitions=previous.repetitions + 1)
        else:
            questions[item.task_id] = item
    if not questions:
        raise ValueError("Empty ToRL dataset")
    return tuple(questions.values())


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


def prepare_torl_benchmark(
    source: Path, output: Path, *, seed: int = 20261017, dev_size: int = 256
) -> Benchmark:
    import pyarrow.parquet as parquet

    def load(split: str) -> tuple[Example, ...]:
        rows = parquet.read_table(source / f"{split}.parquet").to_pylist()
        return torl_examples(
            tuple(decode_record(ToRLRow, row) for row in rows), training=split == "train"
        )

    train, test = load("train"), load("test")
    test_ids = {item.task_id for item in test}
    eligible = [item for item in train if item.task_id not in test_ids]
    if not 1 <= dev_size < len(eligible):
        raise ValueError("Dev split must leave a nonempty independent training pool")
    random.Random(f"{seed}:dev").shuffle(eligible)
    dev, training = tuple(eligible[:dev_size]), tuple(eligible[dev_size:])
    output.mkdir(parents=True, exist_ok=False)
    result = Benchmark(
        seed,
        file_ref(source / "train.parquet"),
        file_ref(source / "test.parquet"),
        write_examples(output / "train.jsonl", training),
        write_examples(output / "dev.jsonl", dev),
        write_examples(output / "heldout/final.jsonl", test),
        tuple(sorted(test_ids | {item.task_id for item in dev})),
    )
    write_record(output / "benchmark.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--format", choices=("torl", "gsm8k"), default="torl")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20261017)
    parser.add_argument("--dev-size", type=int, default=256)
    parser.add_argument("--final-size", type=int, default=512)
    args = parser.parse_args()
    if args.format == "torl":
        result = prepare_torl_benchmark(
            args.source, args.output.resolve(), seed=args.seed, dev_size=args.dev_size
        )
    else:
        result = prepare_benchmark(
            args.source,
            args.output.resolve(),
            seed=args.seed,
            dev_size=args.dev_size,
            final_size=args.final_size,
        )
    print(f"benchmark={args.output.resolve() / 'benchmark.json'}")
    print(f"train_sha256={result.train.digest} dev_sha256={result.dev.digest}")
