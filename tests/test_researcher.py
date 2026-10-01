from __future__ import annotations

import io
import json
from urllib.request import Request

import pytest
from areal_rsi_tir import proposer as harness

from areal_rsi.api.records import RsiCaseScore, RsiEvaluationReport, RsiSplit, Task
from areal_rsi.api.research import ResearcherSpec


def test_researcher_receives_only_public_question_and_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = Task("case1", {"messages": [{"role": "user", "content": "Compute 2 plus 3"}]})
    report = RsiEvaluationReport(
        "sha256:" + "1" * 64,
        "harness_h0",
        "taskset_t0",
        RsiSplit.HARNESS_EVOLVE,
        "sha256:" + "2" * 64,
        "sha256:" + "3" * 64,
        1,
        1.0,
        32,
        (RsiCaseScore("case1", 0.0, "4", 1),),
        "/tmp/evolve.trajectories.json",
        "sha256:" + "4" * 64,
    )
    sent: list[Request] = []

    def fake_open(request: Request) -> io.BytesIO:
        sent.append(request)
        return io.BytesIO(
            json.dumps(
                {"choices": [{"message": {"content": "Use the calculator. {prompt}"}}]}
            ).encode()
        )

    monkeypatch.setattr(harness, "_open", fake_open)
    result = harness.propose_tir_prompt(
        ResearcherSpec("http://127.0.0.1:8000/v1/chat/completions", "researcher", None),
        "Original {prompt}",
        (task,),
        report,
    )
    assert result == "Use the calculator. {prompt}\n"
    body = json.loads(sent[0].data)
    feedback = body["messages"][1]["content"]
    assert "Compute 2 plus 3" in feedback
    assert '"answer"' not in feedback
    assert "sealed_test" not in feedback


def test_researcher_rejects_question_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    question = "This public question is intentionally longer than forty characters"
    task = Task("case1", {"messages": [{"role": "user", "content": question}]})
    report = RsiEvaluationReport(
        "sha256:" + "1" * 64,
        "harness_h0",
        "taskset_t0",
        RsiSplit.HARNESS_EVOLVE,
        "sha256:" + "2" * 64,
        "sha256:" + "3" * 64,
        1,
        1.0,
        32,
        (RsiCaseScore("case1", 0.0, "wrong", 1),),
        "/tmp/evolve.trajectories.json",
        "sha256:" + "4" * 64,
    )

    def fake_open(request: Request) -> io.BytesIO:
        del request
        return io.BytesIO(
            json.dumps({"choices": [{"message": {"content": question + " {prompt}"}}]}).encode()
        )

    monkeypatch.setattr(harness, "_open", fake_open)
    with pytest.raises(ValueError, match="copied"):
        harness.propose_tir_prompt(
            ResearcherSpec("http://localhost:8000/v1/chat/completions", "m", None),
            "Original {prompt}",
            (task,),
            report,
        )


def test_researcher_rejects_unusable_template(monkeypatch: pytest.MonkeyPatch) -> None:
    task = Task("case1", {"messages": [{"role": "user", "content": "Compute 2 plus 3"}]})
    report = RsiEvaluationReport(
        "sha256:" + "1" * 64,
        "harness_h0",
        "taskset_t0",
        RsiSplit.HARNESS_EVOLVE,
        "sha256:" + "2" * 64,
        "sha256:" + "3" * 64,
        1,
        1.0,
        32,
        (RsiCaseScore("case1", 0.0, "4", 1),),
        "/tmp/evolve.trajectories.json",
        "sha256:" + "4" * 64,
    )

    def fake_open(_request: Request) -> io.BytesIO:
        return io.BytesIO(
            json.dumps(
                {"choices": [{"message": {"content": "Use \\boxed{} for {prompt}"}}]}
            ).encode()
        )

    monkeypatch.setattr(harness, "_open", fake_open)
    with pytest.raises(ValueError, match="exactly one"):
        harness.propose_tir_prompt(
            ResearcherSpec("http://127.0.0.1:8000/v1/chat/completions", "researcher", None),
            "Original {prompt}",
            (task,),
            report,
        )
