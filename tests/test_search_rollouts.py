"""Task-level results and native AReaL dumps, without a backend audit callback."""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
import threading
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from areal_rsi.api.control import decode_record
from areal_rsi.api.records import EvaluationSample
from areal_rsi.infra.artifacts import write_record
from examples.search.task.dataset import SearchQuestion
from examples.search.task.rollouts import (
    Episode,
    TokenUsage,
    read_rollout_usage,
    write_evaluation_samples,
)


def episode(question: str, task_id: int, *, sample: int = 0, reward: float = 0.0) -> Episode:
    return Episode(
        question,
        task_id,
        sample,
        reward,
        "Exact Answer: answer",
        1,
        ("doc-1",),
        2.5,
        ({"role": "assistant", "content": f"Sample {sample}: answer"},),
    )


def native_row(task_id: int, sample_idx: int, prompt_len: int, seqlen: int) -> dict[str, int]:
    return dict(task_id=task_id, sample_idx=sample_idx, prompt_len=prompt_len, seqlen=seqlen)


def dump(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_native_usage_counts_repeated_prompts_and_joins_tasks_independent_of_order(
    tmp_path: Path,
) -> None:
    native = tmp_path / "native"
    dump(native / "7/12.jsonl", [native_row(12, 0, 10, 13), native_row(12, 1, 18, 22)])
    dump(native / "7/8.jsonl", [native_row(8, 0, 5, 7)])
    assert read_rollout_usage(native) == {8: TokenUsage(5, 2), 12: TokenUsage(28, 7)}
    write_record(tmp_path / "episodes/z.json", episode("q1", 12))
    write_record(tmp_path / "episodes/a.json", episode("q2", 8, reward=1.0))
    questions = tuple(
        SearchQuestion(q, "Question?", ("answer",), ("doc-1", "doc-2"), (), "dev", q, "hard")
        for q in ("q1", "q2")
    )
    write_evaluation_samples(tmp_path, questions, native)
    rows = [
        decode_record(EvaluationSample, json.loads(line))
        for line in (tmp_path / "samples.jsonl").read_text().splitlines()
    ]
    assert [row.task_id for row in rows] == ["q1", "q2"]
    assert [row.passed for row in rows] == [False, True]
    assert [row.metrics["input_tokens"] for row in rows] == [28, 5]
    assert [row.metrics["output_tokens"] for row in rows] == [7, 2]
    assert all(row.metrics["evidence_recall"] == 0.5 for row in rows)
    assert not list(tmp_path.rglob("*.audit.json"))


@pytest.mark.parametrize("fault", ["duplicate", "invalid_lengths", "merged_export", "missing"])
def test_native_usage_rejects_ambiguous_or_missing_accounting(tmp_path: Path, fault: str) -> None:
    row: dict[str, Any] = native_row(3, 0, 10, 12)
    if fault == "invalid_lengths":
        row["prompt_len"] = 13
    elif fault == "merged_export":
        row["segments"] = [{"role": "gen", "len": 2}]
    if fault != "missing":
        dump(tmp_path / "0/3.jsonl", [row])
    if fault == "duplicate":
        dump(tmp_path / "1/3.jsonl", [row])
    with pytest.raises(ValueError):
        read_rollout_usage(tmp_path)


@pytest.mark.parametrize("fault", ["missing_task", "extra_task", "duplicate_question", "failed"])
def test_native_results_cannot_silently_drop_or_misattribute_a_question(
    tmp_path: Path,
    fault: str,
) -> None:
    item = SearchQuestion("q", "Question?", ("answer",), ("doc-1",), (), "dev", "q", "hard")
    result = episode("q", 3)
    if fault == "failed":
        result = replace(result, failure_type="SearchInfrastructureError")
    write_record(tmp_path / "episodes/one.json", result)
    if fault == "duplicate_question":
        write_record(tmp_path / "episodes/two.json", result)
    task_id = 4 if fault == "missing_task" else 3
    rows = [native_row(task_id, 0, 10, 12)]
    if fault == "extra_task":
        rows.append(native_row(5, 0, 10, 12))
    native = tmp_path / "native"
    dump(native / "0/3.jsonl", rows)
    with pytest.raises(ValueError):
        write_evaluation_samples(tmp_path, (item,), native)
    assert not (tmp_path / "samples.jsonl").exists()


def agent_context(monkeypatch: pytest.MonkeyPatch) -> Any:
    module = ModuleType("areal")
    module.workflow_context = SimpleNamespace(  # type: ignore[attr-defined]
        get=lambda: SimpleNamespace(task_id=3, sample_idx=0),
    )
    monkeypatch.setitem(sys.modules, "areal", module)
    return importlib.import_module("examples.search.task.agent")


@pytest.mark.parametrize("failed", [False, True])
def test_agent_persists_outcome_without_a_tensor_callback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed: bool,
) -> None:
    module = agent_context(monkeypatch)
    agent = module.BrowseCompTrainAgent("unused", "unused", str(tmp_path))
    result = episode("q", 3)
    failure = module.SearchInfrastructureError("tool outage")
    if failed:
        result = replace(result, failure_type="SearchInfrastructureError", final_answer="")

    def run(*args: object) -> Episode:
        if failed:
            raise module.EpisodeRunError(result, failure)
        return result

    monkeypatch.setattr(agent, "_run_sync", run)
    if failed:
        with pytest.raises(module.SearchInfrastructureError):
            asyncio.run(agent.run({"question_id": "q"}, base_url="unused", api_key="private-key"))
    else:
        assert (
            asyncio.run(
                agent.run(
                    {"question_id": "q"},
                    base_url="unused",
                    api_key="private-key",
                )
            )
            == 0.0
        )
    paths = list(tmp_path.glob("*.json"))
    assert len(paths) == 1
    assert decode_record(Episode, json.loads(paths[0].read_text())) == result
    assert "private-key" not in paths[0].read_text()
    assert agent.classify_proxy_failure(failure, context_overflow=True) == "system_failure_reject"


def test_cancellation_drains_the_model_request_before_releasing_the_agent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = agent_context(monkeypatch)
    agent = module.BrowseCompTrainAgent("unused", "unused", str(tmp_path))
    started, finish = threading.Event(), threading.Event()

    def run(*args: object) -> Episode:
        started.set()
        if not finish.wait(5):
            raise TimeoutError("Test did not release the model request")
        return episode("q", 3)

    monkeypatch.setattr(agent, "_run_sync", run)

    async def cancel() -> None:
        task = asyncio.create_task(
            agent.run(
                {"question_id": "q"},
                base_url="unused",
                api_key="private-key",
            )
        )
        try:
            assert await asyncio.to_thread(started.wait, 2)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel())


def test_g4_pilot_uses_episode_results_without_tensor_audit_files(tmp_path: Path) -> None:
    pilot = importlib.import_module("examples.search.training.pilot")
    for sample in range(4):
        write_record(tmp_path / f"episodes/{sample}.json", episode("q", 3, sample=sample))
    report = pilot.summarize_episodes(tmp_path, {"q"}, 4)
    assert report.questions == 1 and report.samples_per_question == 4
    assert report.all_zero_fraction == 1 and report.groups[0].unique_trajectories == 4
    assert len(report.groups[0].episode_files) == 4
    (tmp_path / "episodes/3.json").unlink()
    with pytest.raises(ValueError, match="sample slots"):
        pilot.summarize_episodes(tmp_path, {"q"}, 4)
