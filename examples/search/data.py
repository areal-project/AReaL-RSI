"""Build disjoint, evidenced search QA splits from pinned external HotpotQA data."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from examples.search.reward import answer_in_text, normalize_answer

Split = Literal["train", "dev", "test"]


@dataclass(frozen=True, slots=True)
class Passage:
    docid: str
    title: str
    text: str


@dataclass(frozen=True, slots=True)
class SearchQuestion:
    question_id: str
    question: str
    answers: tuple[str, ...]
    evidence_docids: tuple[str, ...]
    source_groups: tuple[str, ...]
    split: Split
    source_id: str
    difficulty: str


@dataclass(frozen=True, slots=True)
class Candidate:
    source_id: str
    question: str
    answer: str
    support_titles: tuple[str, ...]
    passages: tuple[Passage, ...]
    difficulty: str


@dataclass(frozen=True, slots=True)
class DatasetConfig:
    train_size: int = 1000
    dev_size: int = 200
    test_size: int = 200
    seed: int = 17

    def __post_init__(self) -> None:
        if min(self.train_size, self.dev_size, self.test_size) < 1:
            raise ValueError("All dataset splits must be nonempty")


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(4 * 1024 * 1024):
            result.update(block)
    return result.hexdigest()


def shingles(text: str) -> frozenset[str]:
    words = normalize_answer(text).split()
    return frozenset(" ".join(words[index : index + 4]) for index in range(len(words) - 3))


def near_duplicate(left: frozenset[str], right: frozenset[str]) -> bool:
    union = left | right
    return bool(union) and len(left & right) / len(union) >= 0.6


def parse_hotpot(row: Mapping[str, Any]) -> Candidate:
    """Validate the external Arrow/JSON boundary before accepting a question."""
    question = str(row["question"]).strip()
    answer = str(row["answer"]).strip()
    if not question or len(question) > 2000 or not normalize_answer(answer):
        raise ValueError("invalid_question_or_answer")
    if normalize_answer(answer) in {"yes", "no"}:
        raise ValueError("boolean_answer")
    if re.search(r"\b" + re.escape(normalize_answer(answer)) + r"\b", normalize_answer(question)):
        raise ValueError("answer_in_question")
    context = row["context"]
    support = row["supporting_facts"]
    titles = tuple(str(title) for title in context["title"])
    sentences = tuple(tuple(str(sentence) for sentence in group) for group in context["sentences"])
    if len(titles) != len(sentences) or len(set(titles)) != len(titles):
        raise ValueError("invalid_context")
    support_titles = tuple(sorted(set(str(title) for title in support["title"])))
    if len(support_titles) < 2 or any(title not in titles for title in support_titles):
        raise ValueError("missing_multihop_evidence")
    if len(support["title"]) != len(support["sent_id"]):
        raise ValueError("invalid_support_indices")
    evidence = []
    for title, index in zip(support["title"], support["sent_id"], strict=True):
        group = sentences[titles.index(title)]
        if index < 0 or index >= len(group):
            raise ValueError("invalid_support_indices")
        # Keep gold facts near the start, within a conservative snippet bound.
        if len("".join(group[: index + 1])) > 1400:
            raise ValueError("evidence_beyond_snippet")
        evidence.append(group[index])
    if not answer_in_text(answer, " ".join(evidence)):
        raise ValueError("answer_not_in_gold_evidence")
    passages = []
    for title, group in zip(titles, sentences, strict=True):
        text = f"{title}\n{''.join(group).strip()}"
        identity = hashlib.sha256(text.encode()).hexdigest()[:24]
        passages.append(Passage(f"hotpot-{identity}", title, text))
    return Candidate(
        str(row["id"]), question, answer, support_titles, tuple(passages), str(row["level"])
    )


def partition(
    candidates: Iterable[Candidate], config: DatasetConfig, forbidden_questions: Iterable[str] = ()
) -> tuple[dict[Split, list[SearchQuestion]], dict[Split, list[Passage]], dict[str, int]]:
    rows = list(candidates)
    random.Random(config.seed).shuffle(rows)
    targets: dict[Split, int] = {
        "train": config.train_size,
        "dev": config.dev_size,
        "test": config.test_size,
    }
    questions: dict[Split, list[SearchQuestion]] = {split: [] for split in targets}
    selected: dict[Split, list[Candidate]] = {split: [] for split in targets}
    owners: dict[str, Split] = {}
    seen_questions: set[str] = set()
    accepted_shingles: list[frozenset[str]] = []
    forbidden = [shingles(question) for question in forbidden_questions]
    rejections: Counter[str] = Counter()
    for row in rows:
        normalized = normalize_answer(row.question)
        signature = shingles(row.question)
        if normalized in seen_questions or any(
            near_duplicate(signature, other) for other in (*forbidden, *accepted_shingles)
        ):
            rejections["question_overlap"] += 1
            continue
        groups = tuple(normalize_answer(title) for title in row.support_titles)
        existing = {owners[group] for group in groups if group in owners}
        if len(existing) > 1:
            rejections["cross_split_evidence"] += 1
            continue
        available = [
            split
            for split, limit in targets.items()
            if len(selected[split]) < limit and (not existing or split in existing)
        ]
        if not available:
            rejections["filled_source_group"] += 1
            continue
        split = max(
            available, key=lambda item: (targets[item] - len(selected[item])) / targets[item]
        )
        selected[split].append(row)
        owners.update(dict.fromkeys(groups, split))
        seen_questions.add(normalized)
        accepted_shingles.append(signature)
        if all(len(selected[item]) == targets[item] for item in targets):
            break
    if any(len(selected[split]) != count for split, count in targets.items()):
        raise ValueError(
            f"Insufficient disjoint validated data: {[(k, len(v)) for k, v in selected.items()]}"
        )
    # Allocate every distractor title to one split as well, so no source document
    # is shared across training, development and the external audit environment.
    for split, group in selected.items():
        for row in group:
            for passage in row.passages:
                owners.setdefault(normalize_answer(passage.title), split)
    corpora: dict[Split, list[Passage]] = {}
    for split, group in selected.items():
        documents: dict[str, Passage] = {}
        for row in group:
            support_ids = tuple(p.docid for p in row.passages if p.title in row.support_titles)
            identity = hashlib.sha256(row.question.encode()).hexdigest()[:24]
            questions[split].append(
                SearchQuestion(
                    f"hotpot-{identity}",
                    row.question,
                    (row.answer,),
                    support_ids,
                    tuple(normalize_answer(title) for title in row.support_titles),
                    split,
                    row.source_id,
                    row.difficulty,
                )
            )
            for passage in row.passages:
                if owners[normalize_answer(passage.title)] == split:
                    documents[passage.docid] = passage
        corpora[split] = sorted(documents.values(), key=lambda passage: passage.docid)
        present = {passage.docid for passage in corpora[split]}
        if any(not set(question.evidence_docids) <= present for question in questions[split]):
            raise ValueError("A question has evidence outside its assigned search environment")
    return questions, corpora, dict(rejections)


def prepare(
    source: Path,
    output: Path,
    config: DatasetConfig,
    forbidden: Path | None = None,
    source_metadata: Path | None = None,
) -> Path:
    import pyarrow.parquet as parquet  # type: ignore[import-not-found]

    if output.exists():
        raise FileExistsError(f"Dataset outputs are immutable: {output}")
    rejected: Counter[str] = Counter()
    candidates = []
    for batch in parquet.ParquetFile(source).iter_batches(batch_size=2048):
        for row in batch.to_pylist():
            try:
                candidates.append(parse_hotpot(row))
            except ValueError as exc:
                rejected[str(exc)] += 1
        print(
            json.dumps(
                {"validated_candidates": len(candidates), "rejected": sum(rejected.values())}
            ),
            flush=True,
        )
    forbidden_questions = []
    if forbidden is not None:
        forbidden_questions = [
            str(json.loads(line)["query"]) for line in forbidden.read_text().splitlines() if line
        ]
    questions, corpora, exclusions = partition(candidates, config, forbidden_questions)
    rejected.update(exclusions)
    output.mkdir(parents=True)
    files = {}
    for split in questions:
        for name, records in (
            (f"{split}.jsonl", questions[split]),
            (f"{split}-corpus.jsonl", corpora[split]),
        ):
            path = output / name
            path.write_text(
                "".join(json.dumps(asdict(row), ensure_ascii=False) + "\n" for row in records)
            )
            files[name] = {"sha256": digest(path), "records": len(records)}
    manifest = {
        "builder_sha256": digest(Path(__file__)),
        "reward_sha256": digest(Path(__file__).with_name("reward.py")),
        "source": {"path": str(source.resolve()), "sha256": digest(source)},
        "source_metadata": {
            "path": str(source_metadata.resolve()),
            "sha256": digest(source_metadata),
        }
        if source_metadata
        else None,
        "config": asdict(config),
        "files": files,
        "rejections": dict(rejected),
        "forbidden_question_source": {"sha256": digest(forbidden)} if forbidden else None,
        "quality": {
            "gold_evidence_present": True,
            "gold_answer_witness": True,
            "source_document_disjoint": True,
            "question_near_duplicate_threshold": 0.6,
        },
    }
    path = output / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--forbidden-questions", type=Path)
    parser.add_argument("--source-metadata", type=Path)
    parser.add_argument("--train-size", type=int, default=1000)
    parser.add_argument("--dev-size", type=int, default=200)
    parser.add_argument("--test-size", type=int, default=200)
    parser.add_argument("--seed", type=int, default=17)
    args = parser.parse_args()
    print(
        prepare(
            args.source,
            args.output,
            DatasetConfig(args.train_size, args.dev_size, args.test_size, args.seed),
            args.forbidden_questions,
            args.source_metadata,
        )
    )


if __name__ == "__main__":
    main()
