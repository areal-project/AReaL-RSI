"""Convert the published ToRL parquet files into auditable, deduplicated TIR input."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from areal_rsi.api.control import json_bytes
from areal_rsi.infra.storage.files import write_bytes


@dataclass(frozen=True, slots=True)
class TorlConversionReport:
    train_source_digest: str
    test_source_digest: str
    train_output_digest: str
    test_output_digest: str
    lineage_digest: str
    source_system_digest: str
    train_rows: int
    train_unique: int
    test_rows: int
    test_unique: int


def _sha256(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _file_digest(path: Path) -> str:
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f"ToRL input must be a regular file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _rows(path: Path) -> list[dict[str, Any]]:
    # pyarrow belongs to the AReaL recipe environment, not RSI's core runtime.
    import pyarrow.parquet as parquet  # type: ignore[import-not-found]

    return cast(
        list[dict[str, Any]],
        parquet.read_table(path, columns=["prompt", "reward_model", "extra_info"]).to_pylist(),
    )


def _convert(
    rows: list[dict[str, Any]],
    *,
    label: str,
) -> tuple[dict[str, tuple[str, str]], dict[str, tuple[str, ...]], str]:
    cases: dict[str, tuple[str, str]] = {}
    lineage: dict[str, list[str]] = {}
    systems: set[str] = set()
    for index, row in enumerate(rows):
        prompt = row.get("prompt")
        reward = row.get("reward_model")
        extra = row.get("extra_info")
        if (
            not isinstance(prompt, list)
            or len(prompt) != 2
            or any(not isinstance(message, dict) for message in prompt)
            or tuple(message.get("role") for message in prompt) != ("system", "user")
            or not isinstance(prompt[0].get("content"), str)
            or not isinstance(prompt[1].get("content"), str)
            or not isinstance(reward, dict)
            or not isinstance(reward.get("ground_truth"), str)
            or not isinstance(extra, dict)
        ):
            raise ValueError(f"{label} row {index} is not a supported ToRL case")
        system = prompt[0]["content"]
        question = prompt[1]["content"].strip()
        answer = reward["ground_truth"].strip()
        if not system.strip() or not question or not answer:
            raise ValueError(f"{label} row {index} has empty system, question, or answer")
        systems.add(system)
        normalized = " ".join(question.casefold().split())
        task_id = hashlib.sha256(normalized.encode()).hexdigest()
        previous = cases.get(task_id)
        if previous and previous[1] != answer:
            raise ValueError(f"{label} contains conflicting answers for task {task_id}")
        if previous is None or question < previous[0]:
            cases[task_id] = (question, answer)
        source_id = extra.get("id")
        lineage.setdefault(task_id, []).append(
            str(source_id) if source_id is not None else str(index)
        )
    if not cases or len(systems) != 1:
        raise ValueError(f"{label} requires cases with one shared ToRL system prompt")
    return cases, {key: tuple(sorted(value)) for key, value in lineage.items()}, next(iter(systems))


def _jsonl(cases: dict[str, tuple[str, str]]) -> bytes:
    return b"".join(
        json_bytes({"messages": [{"role": "user", "content": question}], "answer": answer}) + b"\n"
        for _, (question, answer) in sorted(cases.items())
    )


def convert_torl_parquet(
    train_source: Path, test_source: Path, destination: Path
) -> TorlConversionReport:
    """Accept identical repeated questions, reject conflicting answers or split overlap."""

    if destination.exists():
        raise FileExistsError(destination)
    train_digest = _file_digest(train_source)
    test_digest = _file_digest(test_source)
    train_rows, test_rows = _rows(train_source), _rows(test_source)
    train, train_lineage, train_system = _convert(train_rows, label="train")
    test, test_lineage, test_system = _convert(test_rows, label="test")
    if train_system != test_system:
        raise ValueError("ToRL train/test use different system prompts")
    if train.keys() & test.keys():
        raise ValueError("ToRL train/test contain overlapping questions")
    train_bytes, test_bytes = _jsonl(train), _jsonl(test)
    lineage_bytes = json_bytes({"train": train_lineage, "test": test_lineage})
    report = TorlConversionReport(
        train_digest,
        test_digest,
        _sha256(train_bytes),
        _sha256(test_bytes),
        _sha256(lineage_bytes),
        _sha256(train_system.encode()),
        len(train_rows),
        len(train),
        len(test_rows),
        len(test),
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".torl-conversion-") as temp:
        staged = Path(temp) / "converted"
        private = staged / "private"
        private.mkdir(parents=True, mode=0o700)
        for name, content in (
            ("train.jsonl", train_bytes),
            ("test.jsonl", test_bytes),
            ("lineage.json", lineage_bytes),
        ):
            path = private / name
            write_bytes(path, content, immutable=True)
            os.chmod(path, 0o600)
        write_bytes(staged / "conversion.json", json_bytes(report), immutable=True)
        staged.rename(destination)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze deduplicated ToRL parquet as TIR JSONL")
    parser.add_argument("--train", type=Path, required=True)
    parser.add_argument("--test", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = convert_torl_parquet(args.train, args.test, args.output)
    print(json.dumps({"output": str(args.output.resolve()), "report": asdict(report)}))


if __name__ == "__main__":
    main()
