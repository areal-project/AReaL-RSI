"""Frozen evaluation splits and an optional ToRL training pool."""

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
    source: Literal["torl-train", "torl-test", "verified-synthetic"]
    family: str = "math"
    difficulty: int = 0
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
    valid: ContentRef
    test: ContentRef
    # Store identities only; synthesis never needs held-out questions/solutions.
    excluded_ids: tuple[str, ...]
    train: ContentRef | None = None
    official_train: ContentRef | None = None
    official_test: ContentRef | None = None


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
    valid: tuple[Example, ...],
    test: tuple[Example, ...],
    output: Path,
    *,
    seed: int = 20261017,
    train: tuple[Example, ...] = (),
    official_train: ContentRef | None = None,
    official_test: ContentRef | None = None,
) -> Benchmark:
    ids = [{item.task_id for item in split} for split in (train, valid, test)]
    if any(ids[i] & ids[j] for i in range(3) for j in range(i)):
        raise ValueError("Training, valid and test data overlap")
    if not valid or not test:
        raise ValueError("Independent nonempty valid and test data are required")
    output.mkdir(parents=True, exist_ok=False)
    result = Benchmark(
        seed=seed,
        valid=write_examples(output / "valid.jsonl", valid),
        test=write_examples(output / "heldout/test.jsonl", test),
        excluded_ids=tuple(sorted(ids[1] | ids[2])),
        train=write_examples(output / "train.jsonl", train) if train else None,
        official_train=official_train,
        official_test=official_test,
    )
    write_record(output / "benchmark.json", result)
    return result


def prepare_torl_benchmark(
    source: Path, output: Path, *, seed: int = 20261017, valid_size: int = 256
) -> Benchmark:
    # Dataset preparation runs in the AReaL environment, which supplies pyarrow.
    import pyarrow.parquet as parquet  # type: ignore[import-not-found]

    def load(split: str) -> tuple[Example, ...]:
        rows = parquet.read_table(source / f"{split}.parquet").to_pylist()
        return torl_examples(
            tuple(decode_record(ToRLRow, row) for row in rows), training=split == "train"
        )

    train, test = load("train"), load("test")
    test_ids = {item.task_id for item in test}
    eligible = [item for item in train if item.task_id not in test_ids]
    if not 1 <= valid_size < len(eligible):
        raise ValueError("Validation split must leave a nonempty independent training pool")
    # Keep the original RNG stream key: renaming the split must not change its questions.
    random.Random(f"{seed}:dev").shuffle(eligible)
    valid, training = tuple(eligible[:valid_size]), tuple(eligible[valid_size:])
    return prepare_benchmark(
        valid,
        test,
        output,
        seed=seed,
        train=training,
        official_train=file_ref(source / "train.parquet"),
        official_test=file_ref(source / "test.parquet"),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--source", type=Path, help="Directory containing ToRL parquet files")
    inputs.add_argument("--valid", type=Path, help="Prepared valid Example JSONL")
    parser.add_argument("--test", type=Path, help="Prepared independent test Example JSONL")
    parser.add_argument("--train", type=Path, help="Optional official training Example JSONL")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20261017)
    parser.add_argument("--valid-size", type=int, default=256)
    args = parser.parse_args()
    if args.source is not None:
        if args.test is not None or args.train is not None:
            parser.error("--test and --train require --valid")
        result = prepare_torl_benchmark(
            args.source, args.output.resolve(), seed=args.seed, valid_size=args.valid_size
        )
    else:
        if args.test is None:
            parser.error("--valid requires --test")
        result = prepare_benchmark(
            read_examples(args.valid),
            read_examples(args.test),
            args.output.resolve(),
            seed=args.seed,
            train=read_examples(args.train) if args.train is not None else (),
        )
    print(f"benchmark={args.output.resolve() / 'benchmark.json'}")
    print(f"train_sha256={result.train.digest if result.train else 'none'}")
    print(f"valid_sha256={result.valid.digest} test_sha256={result.test.digest}")
