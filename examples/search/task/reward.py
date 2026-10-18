"""Conservative answer rewards; reasoning mentions never count as final answers."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence


def normalize_answer(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = "".join(" " if unicodedata.category(char).startswith("P") else char for char in value)
    value = re.sub(r"\b(a|an|the)\b", " ", value)
    return " ".join(value.split())


def answer_in_text(answer: str, text: str) -> bool:
    needle = normalize_answer(answer)
    return bool(needle) and f" {needle} " in f" {normalize_answer(text)} "


def extract_answer(response: str) -> str | None:
    # Qwen reasoning can repeat or propose final-answer markers before </think>.
    response = response.rsplit("</think>", 1)[-1]
    matches = list(
        re.finditer(r"(?im)^\s*(?:#{1,6}\s*)?(?:\*\*)?Exact Answer(?:\*\*)?\s*:", response)
    )
    if not matches:
        return None
    answer = response[matches[-1].end() :]
    answer = re.split(r"(?im)^\s*(?:\*\*)?Confidence(?:\*\*)?\s*:", answer, maxsplit=1)[0]
    answer = re.sub(r"\[\[?[^\]\n]+\]\]?", "", answer).strip().strip("*` \n")
    return answer or None


def answer_reward(response: str, answers: Sequence[str]) -> float:
    if not answers or any(not normalize_answer(answer) for answer in answers):
        raise ValueError("Reward requires nonempty reference answers")
    prediction = extract_answer(response)
    if prediction is None:
        return 0.0
    expected = {normalize_answer(answer) for answer in answers}
    return float(normalize_answer(prediction) in expected)
