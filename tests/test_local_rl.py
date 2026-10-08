"""Check the local smoke reward cannot credit a wrong or explanatory answer."""

import pytest

from areal_rsi.examples.tir.verifier import arithmetic_reward


@pytest.mark.parametrize(
    ("completion", "expected"),
    [
        ("3", 1.0),
        ("3<|im_end|>", 1.0),
        ("<think>reasoning</think>3<|im_end|>", 1.0),
        ("<think>reasoning</think>3", 1.0),
        (r"\boxed{3}", 1.0),
        ("4", 0.0),
        ("4<|im_end|>", 0.0),
        ("3<|im_end|>4", 0.0),
        ("3 or 4", 0.0),
        ("The answer might be 3", 0.0),
    ],
)
def test_arithmetic_reward_checks_the_final_answer(completion: str, expected: float) -> None:
    assert arithmetic_reward("1+2", completion, [], [], "3") == expected
