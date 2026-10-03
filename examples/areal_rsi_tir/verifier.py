"""Strict, independent TIR verifier used by trusted RSI evaluation."""

from __future__ import annotations

import logging
import re
from itertools import product
from typing import Any

_BOXED_CLOCK = re.compile(r"\\boxed\{\s*([0-9]{1,2}):(00)\s*\}")


def _constant_counterexample(expected: Any, actual: Any) -> bool:
    """Prove a symbolic prediction differs from a rational gold at an exact point."""
    from math_verify.errors import TimeoutException  # type: ignore
    from math_verify.grader import is_equation, take_last_relation  # type: ignore
    from math_verify.utils import timeout  # type: ignore
    from sympy import Expr, Integer, Rational, preorder_traversal  # type: ignore

    @timeout(timeout_seconds=5)  # type: ignore[untyped-decorator]
    def check() -> bool:
        if not isinstance(expected, Rational):
            return False
        # Match the grader's scalar-gold handling of equation chains exactly.
        prediction = take_last_relation(actual).rhs if is_equation(actual) else actual
        if not isinstance(prediction, Expr) or not prediction.free_symbols:
            return False
        symbols = prediction.free_symbols
        if len(symbols) > 3:
            return False
        points = {1, 2, 3, 5}
        for index, node in enumerate(preorder_traversal(prediction)):
            if index >= 256:
                return False
            if isinstance(node, Integer) and 0 < abs(node) <= 10000:
                points.add(int(abs(node)) + 1)
        for point in sorted(points):
            number = Integer(point)
            if any(
                getattr(number, "is_" + assumption, None) != value
                for symbol in symbols
                for assumption, value in symbol.assumptions0.items()
            ):
                continue
            result = prediction.subs(dict.fromkeys(symbols, number)).doit()
            # Singular, floating, and unresolved values are not certificates.
            # Agreement at sampled points is never evidence of equality.
            if isinstance(result, Rational) and result != expected:
                return True
        return False

    try:
        return bool(check())
    except TimeoutException:
        return False


def _capped_nonnegative_integer(expression: Any, cap: int, depth: int = 64) -> int | None:
    """Return min(expression, cap) for nonnegative integer arithmetic, or unknown."""
    from sympy import Add, Integer, Mul, Pow, factorial

    if depth == 0:
        return None
    if isinstance(expression, Integer):
        return min(int(expression), cap) if expression >= 0 else None
    if isinstance(expression, factorial):
        # Find the smallest n with n! >= cap without expanding the argument.
        threshold, value = 0, 1
        while value < cap:
            threshold += 1
            value *= threshold
        argument = _capped_nonnegative_integer(expression.args[0], threshold, depth - 1)
        if argument is None:
            return None
        if argument == threshold:
            return cap
        value = 1
        for factor in range(2, argument + 1):
            value *= factor
        return value
    if isinstance(expression, (Add, Mul)):
        addition = isinstance(expression, Add)
        value = 0 if addition else 1
        for argument in expression.args:
            part = _capped_nonnegative_integer(argument, cap, depth - 1)
            if part is None:
                return None
            value = min(value + part if addition else value * part, cap)
        return value
    if isinstance(expression, Pow):
        base = _capped_nonnegative_integer(expression.base, cap, depth - 1)
        limit = cap.bit_length()
        exponent = _capped_nonnegative_integer(expression.exp, limit, depth - 1)
        if base is None or exponent is None:
            return None
        if exponent == 0:
            return 1
        if base <= 1:
            return base
        # For base >= 2, exponent >= bit_length(cap) implies value >= cap.
        if exponent == limit:
            return cap
        value = 1
        for _ in range(exponent):
            value = min(value * base, cap)
        return value
    return None


def _integer_bound_counterexample(expected: Any, actual: Any) -> bool:
    """Prove an integer prediction exceeds a small rational without expanding it."""
    from sympy import Rational

    if not isinstance(expected, Rational):
        return False
    numerator, denominator = int(expected.p), int(expected.q)
    if max(numerator.bit_length(), denominator.bit_length()) > 256:
        return False
    cap = max(2, numerator // denominator + 1)
    value = _capped_nonnegative_integer(actual, cap)
    # Saturation gives a lower bound, never an equality certificate.
    return value is not None and value * denominator > numerator


def _bounded_integer(expression: Any, depth: int) -> int | None:
    """Evaluate integer exponents only when every intermediate fits in 4096 bits."""
    from sympy import Add, Integer, Mul, Pow

    if depth == 0:
        return None
    if isinstance(expression, Integer):
        value = int(expression)
        return value if value.bit_length() <= 4096 else None
    if isinstance(expression, (Add, Mul)):
        addition = isinstance(expression, Add)
        value = 0 if addition else 1
        for argument in expression.args:
            part = _bounded_integer(argument, depth - 1)
            if part is None:
                return None
            value = value + part if addition else value * part
            if value.bit_length() > 4096:
                return None
        return value
    if isinstance(expression, Pow):
        base = _bounded_integer(expression.base, depth - 1)
        exponent = _bounded_integer(expression.exp, depth - 1)
        if base is None or exponent is None or exponent < 0:
            return None
        if abs(base) > 1 and base.bit_length() * exponent > 4096:
            return None
        return int(pow(base, exponent))
    return None


def _rational_residue(expression: Any, prime: int, depth: int = 64) -> int | None:
    """Reduce exact rational arithmetic without expanding large integer powers."""
    from sympy import Add, Mul, Pow, Rational

    if depth == 0:
        return None
    if isinstance(expression, Rational):
        numerator, denominator = int(expression.p), int(expression.q)
        if denominator % prime == 0:
            return None
        return numerator % prime * pow(denominator, -1, prime) % prime
    if isinstance(expression, (Add, Mul)):
        addition = isinstance(expression, Add)
        value = 0 if addition else 1
        for argument in expression.args:
            residue = _rational_residue(argument, prime, depth - 1)
            if residue is None:
                return None
            value = (value + residue if addition else value * residue) % prime
        return value
    if isinstance(expression, Pow):
        exponent = _bounded_integer(expression.exp, depth - 1)
        if exponent is None:
            return None
        base = _rational_residue(expression.base, prime, depth - 1)
        if base is not None:
            try:
                return pow(base, exponent, prime)
            except ValueError:  # The denominator is not invertible modulo this prime.
                pass
    return None


def strict_math_reward(response: str, answer: str) -> float:
    """Score a response; propagate parser, grader, and timeout failures.

    A malformed model answer normally parses to an empty result and scores zero.
    Exceptions mean the verifier did not produce a valid observation and must
    invalidate the evaluation instead of being recorded as a model failure.
    """

    from math_verify.errors import TimeoutException
    from math_verify.grader import verify
    from math_verify.parser import (  # type: ignore
        ExprExtractionConfig,
        LatexExtractionConfig,
        parse,
    )

    # ToRL includes clock answers such as 11:00. LaTeX math parsing treats
    # that colon as division, producing infinity. Restrict this exception to
    # zero minutes: other two-digit denominators can be ratios (e.g. 19:16).
    clock = _BOXED_CLOCK.fullmatch(answer.strip())
    if clock is not None and int(clock[1]) < 24 and int(clock[2]) < 60:

        def clock_text(match: re.Match[str]) -> str:
            return r"\boxed{\text{" + match[1] + ":" + match[2] + "}}"

        answer = _BOXED_CLOCK.sub(clock_text, answer)
        response = _BOXED_CLOCK.sub(clock_text, response)

    extraction = (ExprExtractionConfig(try_extract_without_anchor=True), LatexExtractionConfig())
    gold = parse(answer, extraction_config=extraction, parsing_timeout=5, raise_on_error=True)
    if not gold:
        raise ValueError("Frozen TIR gold answer cannot be parsed")
    prediction = parse(
        response, extraction_config=extraction, parsing_timeout=5, raise_on_error=True
    )
    if not prediction:
        return 0.0
    try:
        return float(
            verify(
                gold,
                prediction,
                float_rounding=6,
                timeout_seconds=5,
                raise_on_error=True,
            )
        )
    except TimeoutException:
        # Raw strings preserve a reproducible diagnostic without formatting
        # SymPy objects, which could itself expand the problematic expression.
        logging.getLogger(__name__).warning(
            "Math comparison timed out; checking exact residues before a bounded retry: "
            "answer=%r response=%r",
            answer,
            response,
        )
        for expected, actual in product(gold, prediction):
            if _constant_counterexample(expected, actual):
                continue
            if _integer_bound_counterexample(expected, actual):
                continue
            residues = (
                (_rational_residue(expected, prime), _rational_residue(actual, prime))
                for prime in (101, 103)
            )
            # Unequal residues prove inequality. Equal residues leave the
            # comparison undecided and must pass through the original grader.
            if any(a is not None and b is not None and a != b for a, b in residues):
                continue
            if verify(
                expected,
                actual,
                float_rounding=6,
                timeout_seconds=30,
                raise_on_error=True,
            ):
                return 1.0
        return 0.0


def strict_tir_reward_fn(
    prompt: str,
    completions: str,
    prompt_ids: list[int],
    completion_ids: list[int],
    answer: str,
    **kwargs: Any,
) -> float:
    """AReaL reward; an ungradable comparison is zero rather than a dead worker."""

    from math_verify.errors import TimeoutException

    del prompt, prompt_ids, completion_ids, kwargs
    try:
        return strict_math_reward(completions, answer)
    except TimeoutException:
        logging.getLogger(__name__).warning(
            "Training math comparison timed out after retry; assigning zero reward: "
            "answer=%r response_prefix=%r",
            answer,
            completions[:256],
        )
        return 0.0
