from __future__ import annotations

import pytest

from areal_rsi.infra.coordination.evaluation.tir import verified_tir_case


def test_tir_verification_excludes_tool_observation_tokens() -> None:
    trajectory = {
        "input_ids": [[1, 2, 3, 4]],
        "loss_mask": [[0, 1, 0, 1]],
        "attention_mask": [[1, 1, 1, 1]],
        "rewards": [1.0],
    }

    def verify(response: str, answer: str) -> float:
        assert answer == "24"
        return float(response == answer)

    case = verified_tir_case(
        "case1",
        trajectory,
        answer="24",
        decode=lambda ids: "".join(str(token) for token in ids),
        verify=verify,
    )
    assert case.response == "24"
    assert case.generated_tokens == 2
    assert case.score == 1.0
    trajectory["rewards"] = [0.0]
    with pytest.raises(ValueError, match="disagrees"):
        verified_tir_case(
            "case1",
            trajectory,
            answer="24",
            decode=lambda ids: "".join(str(token) for token in ids),
            verify=verify,
        )
