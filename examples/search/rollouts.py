"""Join task outcomes with AReaL's native individual-export rollout dumps."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from areal_rsi.api.control import encode_record
from areal_rsi.api.records import EvaluationSample, TraceEvent
from areal_rsi.infra.artifacts import read_record
from examples.search.data import SearchQuestion


@dataclass(frozen=True, slots=True)
class Episode:
    question_id: str
    task_id: int
    sample_index: int | None
    reward: float
    final_answer: str
    search_calls: int
    retrieved_docids: tuple[str, ...]
    elapsed_seconds: float
    messages: tuple[dict[str, Any], ...]  # Opaque Qwen-Agent message boundary.
    failure_type: str = ""


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0


def read_rollout_usage(directory: Path) -> dict[int, TokenUsage]:
    """Count every model call, including repeated prompt tokens, once per task."""
    usage: dict[int, TokenUsage] = {}
    seen: set[tuple[int, int]] = set()
    for path in sorted(directory.glob("*/*.jsonl")):
        with path.open() as stream:
            for line in stream:
                record = json.loads(line)
                task_id, sample_idx, prompt_len, seqlen = (
                    record[key] for key in ("task_id", "sample_idx", "prompt_len", "seqlen")
                )
                if (
                    any(
                        type(value) is not int or value < 0
                        for value in (task_id, sample_idx, prompt_len, seqlen)
                    )
                    or prompt_len > seqlen
                    or record.get("segments")
                ):
                    raise ValueError(f"Expected native individual-export token lengths: {path}")
                key = (task_id, sample_idx)
                if key in seen:
                    raise ValueError(f"Duplicate native rollout row: {key}")
                seen.add(key)
                previous = usage.get(task_id, TokenUsage())
                usage[task_id] = TokenUsage(
                    previous.input_tokens + prompt_len,
                    previous.output_tokens + seqlen - prompt_len,
                )
    if not usage:
        raise ValueError(f"No native rollout records: {directory}")
    return usage


def write_evaluation_samples(
    output: Path, questions: tuple[SearchQuestion, ...], rollout_directory: Path
) -> None:
    episodes: dict[str, Episode] = {}
    for path in (output / "episodes").glob("*.json"):
        episode = read_record(path, Episode)
        if episode.question_id in episodes:
            raise ValueError("Duplicate independent evaluation trajectory")
        if episode.failure_type:
            raise ValueError("Independent evaluation contains a failed episode")
        episodes[episode.question_id] = episode
    expected = {question.question_id for question in questions}
    if not expected or len(expected) != len(questions) or set(episodes) != expected:
        raise ValueError("Independent evaluation traces are incomplete")
    usage = read_rollout_usage(rollout_directory)
    task_ids = {episode.task_id for episode in episodes.values()}
    if len(task_ids) != len(episodes) or set(usage) != task_ids:
        raise ValueError("Native rollout tasks differ from the evaluation episodes")
    with (output / "samples.jsonl").open("x") as stream:
        for question in questions:
            episode = episodes[question.question_id]
            tokens = usage[episode.task_id]
            evidence = set(question.evidence_docids)
            recall = len(evidence & set(episode.retrieved_docids)) / len(evidence)
            events = [TraceEvent("prompt", question.question)]
            for message in episode.messages:
                kind: Literal["tool_result", "tool_call", "generation"] = (
                    "tool_result"
                    if message["role"] == "function"
                    else ("tool_call" if message.get("function_call") else "generation")
                )
                events.append(TraceEvent(kind, json.dumps(message, ensure_ascii=False)))
            sample = EvaluationSample(
                question.question_id,
                episode.final_answer,
                episode.reward,
                episode.reward == 1.0,
                tuple(events),
                {
                    "evidence_recall": recall,
                    "search_calls": float(episode.search_calls),
                    "elapsed_seconds": episode.elapsed_seconds,
                    "input_tokens": float(tokens.input_tokens),
                    "output_tokens": float(tokens.output_tokens),
                },
            )
            stream.write(json.dumps(encode_record(sample), ensure_ascii=False) + "\n")
