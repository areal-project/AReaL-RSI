"""Strict, independent TIR verifier used by trusted RSI evaluation."""

from __future__ import annotations

from typing import Any


def strict_math_reward(response: str, answer: str) -> float:
    """Score a response; propagate parser, grader, and timeout failures.

    A malformed model answer normally parses to an empty result and scores zero.
    Exceptions mean the verifier did not produce a valid observation and must
    invalidate the evaluation instead of being recorded as a model failure.
    """

    from math_verify.grader import verify  # type: ignore[import-not-found]
    from math_verify.parser import (  # type: ignore[import-not-found]
        ExprExtractionConfig,
        LatexExtractionConfig,
        parse,
    )

    extraction = (ExprExtractionConfig(try_extract_without_anchor=True), LatexExtractionConfig())
    gold = parse(answer, extraction_config=extraction, parsing_timeout=5, raise_on_error=True)
    if not gold:
        raise ValueError("Frozen TIR gold answer cannot be parsed")
    prediction = parse(
        response, extraction_config=extraction, parsing_timeout=5, raise_on_error=True
    )
    if not prediction:
        return 0.0
    return float(
        verify(
            gold,
            prediction,
            float_rounding=6,
            timeout_seconds=5,
            raise_on_error=True,
        )
    )


def strict_tir_reward_fn(
    prompt: str,
    completions: str,
    prompt_ids: list[int],
    completion_ids: list[int],
    answer: str,
    **kwargs: Any,
) -> float:
    """AReaL TIR callback with the same strict reward as independent evaluation."""

    del prompt, prompt_ids, completion_ids, kwargs
    return strict_math_reward(completions, answer)
