"""Freeze a TIR task set before Harness search or model training begins."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from areal_rsi.api.control import decode, json_bytes
from areal_rsi.api.records import RsiSplit, Task, TaskPartition, TaskSetVersion
from areal_rsi.infra.storage.files import write_bytes


@dataclass(frozen=True, slots=True)
class TirSplitSizes:
    harness_evolve: int
    harness_select: int
    model_train: int
    model_dev: int

    def __post_init__(self) -> None:
        if any(
            type(size) is not int or size < 1
            for size in (
                self.harness_evolve,
                self.harness_select,
                self.model_train,
                self.model_dev,
            )
        ):
            raise ValueError("Every TIR training split needs a positive integer size")

    @property
    def total(self) -> int:
        return sum(
            (
                self.harness_evolve,
                self.harness_select,
                self.model_train,
                self.model_dev,
            )
        )


@dataclass(frozen=True, slots=True)
class TirCase:
    task_id: str
    question: str
    answer: str


def _sha256(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _regular_bytes(path: Path) -> bytes:
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f"TIR source must be a regular file: {path}")
    return path.read_bytes()


def _load_cases(source: bytes, *, label: str) -> tuple[TirCase, ...]:
    cases: list[TirCase] = []
    questions: set[str] = set()
    for line_number, line in enumerate(source.splitlines(), 1):
        if not line.strip():
            continue
        try:
            item: Any = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{label}:{line_number} is not JSON") from exc
        if not isinstance(item, dict) or set(item) != {"messages", "answer"}:
            raise ValueError(f"{label}:{line_number} needs exactly messages and answer")
        messages = item["messages"]
        answer = item["answer"]
        if (
            not isinstance(messages, list)
            or len(messages) != 1
            or not isinstance(messages[0], dict)
            or set(messages[0]) != {"role", "content"}
            or messages[0]["role"] != "user"
            or not isinstance(messages[0]["content"], str)
            or not messages[0]["content"].strip()
            or not isinstance(answer, str)
            or not answer.strip()
        ):
            raise ValueError(f"{label}:{line_number} has an invalid TIR question or answer")
        question = messages[0]["content"].strip()
        normalized = " ".join(question.casefold().split())
        if normalized in questions:
            raise ValueError(f"Duplicate TIR question in {label}: line {line_number}")
        questions.add(normalized)
        task_id = hashlib.sha256(normalized.encode()).hexdigest()
        cases.append(TirCase(task_id, question, answer.strip()))
    if not cases:
        raise ValueError(f"{label} has no TIR cases")
    return tuple(cases)


def _public_bytes(cases: tuple[TirCase, ...]) -> bytes:
    return b"".join(
        json_bytes(
            {"task_id": case.task_id, "messages": [{"role": "user", "content": case.question}]}
        )
        + b"\n"
        for case in cases
    )


def _partition(cases: tuple[TirCase, ...], path: Path) -> TaskPartition:
    content = _public_bytes(cases)
    write_bytes(path, content, immutable=True)
    return TaskPartition(tuple(case.task_id for case in cases), _sha256(content))


def _identity(version: TaskSetVersion) -> str:
    payload = {
        "source_train_digest": version.source_train_digest,
        "source_test_digest": version.source_test_digest,
        "answers_digest": version.answers_digest,
        "harness_evolve": version.harness_evolve,
        "harness_select": version.harness_select,
        "model_train": version.model_train,
        "model_dev": version.model_dev,
        "sealed_test": version.sealed_test,
    }
    return "taskset_" + hashlib.sha256(json_bytes(payload)).hexdigest()


def freeze_tir_task_set(
    train_source: Path,
    test_source: Path,
    destination: Path,
    *,
    sizes: TirSplitSizes,
    seed: int,
) -> TaskSetVersion:
    """Write disjoint public splits and private answers as one atomic directory."""

    if type(seed) is not int or seed < 0:
        raise ValueError("TIR split seed must be a nonnegative integer")
    if destination.exists():
        raise FileExistsError(destination)
    train_bytes = _regular_bytes(train_source)
    test_bytes = _regular_bytes(test_source)
    train = _load_cases(train_bytes, label="train")
    test = _load_cases(test_bytes, label="test")
    if sizes.total > len(train):
        raise ValueError("TIR train source has fewer unique cases than the requested splits")
    if {case.task_id for case in train} & {case.task_id for case in test}:
        raise ValueError("TIR official train and test contain overlapping questions")
    ordered = sorted(
        train,
        key=lambda case: hashlib.sha256(f"{seed}:{case.task_id}".encode()).digest(),
    )
    cursor = 0
    groups: dict[RsiSplit, tuple[TirCase, ...]] = {}
    for split, size in (
        (RsiSplit.HARNESS_EVOLVE, sizes.harness_evolve),
        (RsiSplit.HARNESS_SELECT, sizes.harness_select),
        (RsiSplit.MODEL_TRAIN, sizes.model_train),
        (RsiSplit.MODEL_DEV, sizes.model_dev),
    ):
        groups[split] = tuple(ordered[cursor : cursor + size])
        cursor += size
    groups[RsiSplit.SEALED_TEST] = test

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".tir-taskset-") as temporary:
        staged = Path(temporary) / "taskset"
        staged.mkdir()
        public = staged / "public"
        public.mkdir()
        partitions = {
            split: _partition(cases, public / f"{split.value}.jsonl")
            for split, cases in groups.items()
        }
        private = staged / "private"
        private.mkdir(mode=0o700)
        answers = {case.task_id: case.answer for cases in groups.values() for case in cases}
        answer_bytes = json_bytes(answers)
        answer_path = private / "answers.json"
        write_bytes(answer_path, answer_bytes, immutable=True)
        os.chmod(answer_path, 0o600)
        provisional = TaskSetVersion(
            task_set_id="taskset_pending",
            source_train_digest=_sha256(train_bytes),
            source_test_digest=_sha256(test_bytes),
            answers_digest=_sha256(answer_bytes),
            harness_evolve=partitions[RsiSplit.HARNESS_EVOLVE],
            harness_select=partitions[RsiSplit.HARNESS_SELECT],
            model_train=partitions[RsiSplit.MODEL_TRAIN],
            model_dev=partitions[RsiSplit.MODEL_DEV],
            sealed_test=partitions[RsiSplit.SEALED_TEST],
        )
        version = TaskSetVersion(
            task_set_id=_identity(provisional),
            source_train_digest=provisional.source_train_digest,
            source_test_digest=provisional.source_test_digest,
            answers_digest=provisional.answers_digest,
            harness_evolve=provisional.harness_evolve,
            harness_select=provisional.harness_select,
            model_train=provisional.model_train,
            model_dev=provisional.model_dev,
            sealed_test=provisional.sealed_test,
        )
        write_bytes(staged / "manifest.json", json_bytes(version), immutable=True)
        staged.rename(destination)
    return version


def freeze_shared_tir_task_set(
    parent_root: Path,
    destination: Path,
    *,
    seed: int,
    train_count: int = 512,
) -> TaskSetVersion:
    """Freeze a shared training pool and reuse the parent's development pool."""
    if type(seed) is not int or seed < 0 or type(train_count) is not int or train_count < 1:
        raise ValueError("Shared task set needs a valid seed and partition sizes")
    parent = load_tir_task_set(parent_root)
    if destination.exists():
        raise FileExistsError(destination)
    if train_count > len(parent.model_train.task_ids):
        raise ValueError("Parent has too few frozen model training questions")
    answers: dict[str, str] = json.loads(_regular_bytes(parent_root / "private/answers.json"))
    training = sorted(
        public_tir_tasks(parent_root, RsiSplit.MODEL_TRAIN),
        key=lambda task: hashlib.sha256(f"{seed}:{task.task_id}".encode()).digest(),
    )[:train_count]
    selected = tuple(
        TirCase(task.task_id, task.input["messages"][0]["content"], answers[task.task_id])
        for task in training
    )
    provenance = json_bytes(
        {
            "parent_task_set_id": parent.task_set_id,
            "parent_source_train_digest": parent.source_train_digest,
            "seed": seed,
            "model_train_ids": [case.task_id for case in selected],
            "harness_evolve_ids": [case.task_id for case in selected],
            "harness_select_ids": list(parent.model_dev.task_ids),
        }
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".tir-shared-") as temporary:
        staged = Path(temporary) / "taskset"
        public, private = staged / "public", staged / "private"
        public.mkdir(parents=True)
        private.mkdir(mode=0o700)
        partitions: dict[RsiSplit, TaskPartition] = {}
        groups = {
            RsiSplit.HARNESS_EVOLVE: selected,
            RsiSplit.MODEL_TRAIN: selected,
        }
        for split in RsiSplit:
            path = public / f"{split.value}.jsonl"
            if split in groups:
                partitions[split] = _partition(groups[split], path)
            else:
                source_split = RsiSplit.MODEL_DEV if split == RsiSplit.HARNESS_SELECT else split
                write_bytes(
                    path,
                    _regular_bytes(parent_root / "public" / f"{source_split.value}.jsonl"),
                    immutable=True,
                )
                partitions[split] = getattr(parent, source_split.value)
        answer_ids = {task_id for split in RsiSplit for task_id in partitions[split].task_ids}
        answer_bytes = json_bytes(
            {task_id: answer for task_id, answer in answers.items() if task_id in answer_ids}
        )
        if set(json.loads(answer_bytes)) != answer_ids:
            raise ValueError("Shared task set answers do not match its public questions")
        write_bytes(private / "answers.json", answer_bytes, immutable=True)
        os.chmod(private / "answers.json", 0o600)
        provisional = TaskSetVersion(
            "taskset_pending",
            _sha256(provenance),
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
        write_bytes(staged / "shared-provenance.json", provenance, immutable=True)
        staged.rename(destination)
    return load_tir_task_set(destination)


def load_tir_task_set(root: Path) -> TaskSetVersion:
    """Reject changed public questions, answers, and task identities."""

    raw_manifest = _regular_bytes(root / "manifest.json")
    version: TaskSetVersion = decode(TaskSetVersion, json.loads(raw_manifest))
    if raw_manifest != json_bytes(version):
        raise ValueError("TIR task set manifest bytes changed")
    if version.task_set_id != _identity(version):
        raise ValueError("TIR task set manifest identity changed")
    for split in RsiSplit:
        partition: TaskPartition = getattr(version, split.value)
        content = _regular_bytes(root / "public" / f"{split.value}.jsonl")
        if _sha256(content) != partition.public_digest:
            raise ValueError(f"TIR public split changed: {split.value}")
        rows = [json.loads(line) for line in content.splitlines() if line.strip()]
        if tuple(row.get("task_id") for row in rows) != partition.task_ids:
            raise ValueError(f"TIR task identities changed: {split.value}")
        if any(set(row) != {"task_id", "messages"} for row in rows):
            raise ValueError(f"TIR public split contains unexpected fields: {split.value}")
    answer_bytes = _regular_bytes(root / "private" / "answers.json")
    if _sha256(answer_bytes) != version.answers_digest:
        raise ValueError("TIR private answers changed")
    answers = json.loads(answer_bytes)
    expected_ids = {
        task_id for split in RsiSplit for task_id in getattr(version, split.value).task_ids
    }
    if (
        not isinstance(answers, dict)
        or set(answers) != expected_ids
        or any(not isinstance(answer, str) or not answer.strip() for answer in answers.values())
    ):
        raise ValueError("TIR private answer identities do not match the task set")
    shared_path = root / "shared-provenance.json"
    if shared_path.exists():
        shared_bytes = _regular_bytes(shared_path)
        shared = json.loads(shared_bytes)
        if (
            _sha256(shared_bytes) != version.source_train_digest
            or shared.get("model_train_ids") != list(version.model_train.task_ids)
            or shared.get("harness_evolve_ids") != list(version.harness_evolve.task_ids)
            or shared.get("harness_select_ids") != list(version.harness_select.task_ids)
            or version.harness_evolve.task_ids != version.model_train.task_ids
            or version.harness_select.task_ids != version.model_dev.task_ids
        ):
            raise ValueError("Shared task set provenance changed")
    return version


def public_tir_tasks(root: Path, split: RsiSplit) -> tuple[Task, ...]:
    version = load_tir_task_set(root)
    partition: TaskPartition = getattr(version, split.value)
    content = _regular_bytes(root / "public" / f"{split.value}.jsonl")
    rows = [json.loads(line) for line in content.splitlines() if line.strip()]
    return tuple(
        Task(task_id, {"messages": row["messages"]})
        for task_id, row in zip(partition.task_ids, rows, strict=True)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze canonical TIR JSONL into RSI task splits")
    parser.add_argument("--train", type=Path, required=True)
    parser.add_argument("--test", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--harness-evolve", type=int, required=True)
    parser.add_argument("--harness-select", type=int, required=True)
    parser.add_argument("--model-train", type=int, required=True)
    parser.add_argument("--model-dev", type=int, required=True)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    version = freeze_tir_task_set(
        args.train,
        args.test,
        args.output,
        sizes=TirSplitSizes(
            args.harness_evolve,
            args.harness_select,
            args.model_train,
            args.model_dev,
        ),
        seed=args.seed,
    )
    print(json.dumps({"task_set_id": version.task_set_id, "output": str(args.output.resolve())}))


if __name__ == "__main__":
    main()
