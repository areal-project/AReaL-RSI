"""Task rewards: exact procedural integers and AReaL TIR's native math verifier."""

from __future__ import annotations

import re


def integer_reward(
    prompt: str,
    completions: str,
    prompt_ids: object,
    completion_ids: object,
    answer: str,
    **kwargs: object,
) -> float:
    matches = re.findall(r"\\boxed\s*\{\s*([+-]?\d[\d,]*)\s*\}", completions)
    return float(bool(matches) and int(matches[-1].replace(",", "")) == int(answer))


def math_reward(
    prompt: str,
    completions: str,
    prompt_ids: object,
    completion_ids: object,
    answer: str,
    **kwargs: object,
) -> float:
    from areal.reward import get_math_verify_worker  # type: ignore[import-not-found]

    return float(get_math_verify_worker().verify(str(completions), str(answer)))
