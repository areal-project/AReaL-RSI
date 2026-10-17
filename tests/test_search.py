"""Search data leakage boundaries and terminal-answer reward behavior."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from areal_rsi.api.control import encode_record
from areal_rsi.api.execution import AReaLRuntime, JobSpec
from areal_rsi.api.records import BenchmarkSplit, EvaluationSample, TraceEvent
from areal_rsi.infra.artifacts import read_record, write_record
from areal_rsi.infra.training import AReaLBackend
from examples.search import pipeline
from examples.search.data import Candidate, DatasetConfig, Passage, parse_hotpot, partition
from examples.search.pipeline import (
    ComputeBudget,
    ComputeJob,
    ExperimentState,
    HarnessConfig,
    SearchConfig,
    TrainingConfig,
    WorkerRequest,
    stage_model,
)
from examples.search.reward import answer_in_text, answer_reward, extract_answer


def candidate(index: int) -> Candidate:
    return Candidate(
        str(index),
        f"Identify nation{index} associated person{index} via university{index} research{index}?",
        f"Country{index}",
        (f"Person{index}", f"University{index}"),
        (
            Passage(f"p{index}", f"Person{index}", f"Person{index} attended University{index}."),
            Passage(f"u{index}", f"University{index}", f"University{index} is in Country{index}."),
            Passage("shared", "Shared distractor", "An unrelated document."),
        ),
        "hard",
    )


def test_partitions_isolate_all_documents_and_retain_evidence() -> None:
    questions, corpora, _ = partition(
        [candidate(index) for index in range(12)], DatasetConfig(2, 2, 2)
    )
    assert {key: len(value) for key, value in questions.items()} == {
        "train": 2,
        "dev": 2,
        "test": 2,
    }
    for split, group in questions.items():
        present = {passage.docid for passage in corpora[split]}
        assert all(set(question.evidence_docids) <= present for question in group)
        for other in questions:
            if other != split:
                assert not present & {passage.docid for passage in corpora[other]}
                assert not {title for question in group for title in question.source_groups} & {
                    title for question in questions[other] for title in question.source_groups
                }


def test_partition_excludes_benchmark_paraphrases_and_is_reproducible() -> None:
    rows = [candidate(index) for index in range(12)]
    first = partition(rows, DatasetConfig(2, 2, 2), [rows[0].question])
    second = partition(rows, DatasetConfig(2, 2, 2), [rows[0].question])
    assert first == second
    assert all(question.source_id != "0" for group in first[0].values() for question in group)


def test_insufficient_disjoint_data_fails_instead_of_reusing_sources() -> None:
    rows = [
        replace(candidate(index), support_titles=("Same person", "Same school"))
        for index in range(9)
    ]
    with pytest.raises(ValueError, match="Insufficient disjoint"):
        partition(rows, DatasetConfig(1, 1, 1))


def test_hotpot_requires_actual_answer_witness_and_valid_support_indices() -> None:
    row = {
        "id": "external-1",
        "question": "Where is the school attended by Maria located?",
        "answer": "Estonia",
        "level": "hard",
        "context": {
            "title": ["Maria", "School"],
            "sentences": [["Maria attended School."], ["School is located in Estonia."]],
        },
        "supporting_facts": {"title": ["Maria", "School"], "sent_id": [0, 0]},
    }
    assert parse_hotpot(row).answer == "Estonia"
    with pytest.raises(ValueError, match="answer_not_in_gold_evidence"):
        parse_hotpot({**row, "answer": "Canada"})
    with pytest.raises(ValueError, match="invalid_support_indices"):
        parse_hotpot({**row, "supporting_facts": {"title": ["Maria", "School"], "sent_id": [0, 5]}})


def test_reward_ignores_reasoning_mentions_and_accepts_only_final_alias() -> None:
    response = (
        "<think>Exact Answer: Estonia</think>\nExplanation: Estonia is a distractor.\n"
        "Exact Answer: Canada\nConfidence: 80%"
    )
    assert answer_reward(response, ["Estonia"]) == 0.0
    assert (
        answer_reward(
            "Explanation: Evidence.\nExact Answer: **The United States.** [doc-1]\nConfidence: 90%",
            ["United States", "USA"],
        )
        == 1.0
    )
    assert answer_reward("Estonia", ["Estonia"]) == 0.0
    assert extract_answer("Exact Answer: \nConfidence: 0%") is None


def test_reward_does_not_accept_partial_or_ambiguous_answers() -> None:
    assert answer_reward("Exact Answer: York\nConfidence: 50%", ["New York"]) == 0.0
    assert answer_reward("Exact Answer: Estonia or Canada\nConfidence: 50%", ["Estonia"]) == 0.0
    with pytest.raises(ValueError, match="reference"):
        answer_reward("Exact Answer: Estonia", [])
    assert not answer_in_text("US", "The business school was founded in 1984.")
    assert answer_in_text("US", "The school is in the US.")


def test_frozen_harness_rejects_silent_budget_changes() -> None:
    assert HarnessConfig().max_model_calls == 50
    with pytest.raises(ValueError, match="frozen original"):
        HarnessConfig(max_model_calls=5)


def test_training_budget_requires_enough_full_batches() -> None:
    config = TrainingConfig(max_steps=200, epochs=1, batch_size=8)
    with pytest.raises(ValueError, match="allow 125 full updates"):
        config.validate_dataset_size(1000)
    replace(config, epochs=2).validate_dataset_size(1000)


def test_budget_reserves_full_active_allocations_including_transitional_states() -> None:
    budget = ComputeBudget(
        256.0,
        120,
        120 / 3600,
        (
            ComputeJob("1", "failed-pilot", 2, "FAILED", elapsed_seconds=60),
            ComputeJob(
                "2", "training-candidate", 5, "RUNNING", elapsed_seconds=10, time_limit_seconds=7200
            ),
            ComputeJob("3", "index", 1, "REQUEUED", time_limit_seconds=600),
        ),
    )
    assert budget.reserved_gpu_seconds == 120 + 5 * 7200 + 600


def test_model_staging_preserves_original_and_rejects_changed_effective_config(
    tmp_path: Path,
) -> None:
    import json

    source = tmp_path / "source"
    source.mkdir()
    original = {"model_type": "qwen3", "max_position_embeddings": 32768}
    (source / "config.json").write_text(json.dumps(original))
    (source / "tokenizer_config.json").write_text("{}")
    output = tmp_path / "staged"
    stage_model(source, output)
    assert json.loads((source / "config.json").read_text()) == original
    assert json.loads((output / "config.json").read_text())["max_position_embeddings"] == 131072
    assert (output / "tokenizer_config.json").resolve() == source / "tokenizer_config.json"
    assert stage_model(source, output) == output
    different_source = tmp_path / "different-source"
    different_source.mkdir()
    (different_source / "config.json").write_text(json.dumps(original))
    (different_source / "tokenizer_config.json").write_text("{}")
    with pytest.raises(ValueError, match="different source"):
        stage_model(different_source, output)
    (output / "config.json").write_text("{}")
    with pytest.raises(ValueError, match="different effective"):
        stage_model(source, output)


def search_config(tmp_path: Path) -> SearchConfig:
    repository = tmp_path / "backend"
    (repository / "areal").mkdir(parents=True)
    (repository / "areal" / "__init__.py").touch()
    return SearchConfig(
        AReaLRuntime(str(repository), sys.executable),
        str(tmp_path / "frozen-best-model"),
        str(tmp_path / "benchmark"),
        str(tmp_path / "data"),
        str(tmp_path / "index"),
        str(tmp_path / "embedding"),
        str(tmp_path / "tokenizer"),
        str(tmp_path / "runs"),
        project=str(tmp_path),
    )


def test_heldout_evaluation_reloads_without_training_or_changing_incumbent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = search_config(tmp_path)
    data = Path(config.dataset_directory)
    data.mkdir()
    # No train/dev files exist: the independent mode must use the requested split.
    (data / "test.jsonl").write_text('{"question_id":"external-test"}\n')
    root = Path(config.output_root)
    root.mkdir()
    incumbent = root / "incumbent.json"
    incumbent.write_text("incumbent must not be read or changed by held-out results")
    original = incumbent.read_bytes()
    monkeypatch.setenv("SLURM_JOB_ID", "123")
    monkeypatch.setenv("SEARCH_SLURM_JOB_INFO", "JobId=123 Reservation=rsi")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0,1")
    worker_requests = []

    def spec(
        self: AReaLBackend, entrypoint: Path, arguments: tuple[str, ...], *, timeout: float
    ) -> JobSpec:
        request = read_record(Path(arguments[1]), WorkerRequest)
        assert request.mode == "eval" and request.dataset == str(data / "test.jsonl")
        assert request.model == config.model
        worker_requests.append(request)
        sample = EvaluationSample(
            "external-test", "answer", 1.0, True, (TraceEvent("generation", "answer"),)
        )
        payload = json.dumps(encode_record(sample)) + "\n"
        command = (
            "from pathlib import Path; "
            f"Path({str(Path(request.output) / 'samples.jsonl')!r}).write_text({payload!r})"
        )
        return JobSpec((sys.executable, "-c", command), cwd=str(tmp_path), timeout_seconds=10)

    real_spawn = asyncio.create_subprocess_exec

    async def spawn(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        if "examples.search.dense" in args:
            assert "test-corpus.jsonl" in str(args) and "test.npy" in str(args)
            return await real_spawn(sys.executable, "-c", "import time; time.sleep(60)", **kwargs)
        return await real_spawn(*args, **kwargs)

    async def ready(process: asyncio.subprocess.Process, url: str) -> None:
        assert process.returncode is None

    monkeypatch.setattr(AReaLBackend, "spec", spec)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(pipeline, "wait_for_dense", ready)
    directory = asyncio.run(pipeline.run(config, "heldout", evaluation_split="test"))
    state = read_record(directory / "experiment.json", ExperimentState)
    assert state.phase == "completed" and state.evaluation_split == "test"
    assert state.training is None and state.baseline is None
    assert state.candidate is not None and state.candidate.report.split == BenchmarkSplit.TEST
    assert state.candidate.report.score == 1 and incumbent.read_bytes() == original
    assert asyncio.run(pipeline.run(config, "heldout", evaluation_split="test")) == directory
    assert len(worker_requests) == 1  # Resume reuses the verified durable result.
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0,1,2,3,4,5,6")
    with pytest.raises(ValueError, match="Resume evaluation split differs"):
        asyncio.run(pipeline.run(config, "heldout"))


def test_heldout_submission_accounts_two_gpus_and_refuses_changed_resume_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = search_config(tmp_path)
    path = tmp_path / "config.json"
    write_record(path, config)
    image = tmp_path / "image.sif"
    image.touch()
    budget_path = tmp_path / "budget.json"
    budget = ComputeBudget(
        256, 0, 0, (ComputeJob("1", "training-candidate", 5, "RUNNING", time_limit_seconds=60),)
    )
    write_record(budget_path, budget)
    monkeypatch.setattr(pipeline, "refresh_budget", lambda path: read_record(path, ComputeBudget))
    submissions = []

    def submit(args: list[str], **kwargs: Any) -> str:
        assert args[0] == "sbatch" and "--reservation=rsi" in args and "--gres=gpu:2" in args
        submissions.append(args)
        return "123\n"

    monkeypatch.setattr(subprocess, "check_output", submit)
    assert (
        pipeline.submit(path, "heldout", image, budget_path, 120, evaluation_split="test") == "123"
    )
    updated = read_record(budget_path, ComputeBudget)
    assert updated.jobs[-1].kind == "external-test-evaluation" and updated.jobs[-1].gpus == 2
    assert updated.reserved_gpu_seconds == 5 * 60 + 2 * 120
    assert (
        pipeline.submit(path, "heldout", image, budget_path, 120, evaluation_split="test") == "123"
    )
    assert len(submissions) == 1
    with pytest.raises(ValueError, match="different configuration or mode"):
        pipeline.submit(path, "heldout", image, budget_path, 120, evaluation_split="dev")
    with pytest.raises(ValueError, match="different configuration or mode"):
        pipeline.submit(path, "heldout", image, budget_path, 120)
