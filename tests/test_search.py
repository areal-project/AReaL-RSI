"""Search data leakage boundaries and terminal-answer reward behavior."""

from __future__ import annotations

import asyncio
import json
import shlex
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
from examples.search.experiment import pipeline, slurm
from examples.search.experiment.config import (
    HarnessConfig,
    SearchConfig,
    TrainingConfig,
    WorkerRequest,
)
from examples.search.experiment.pipeline import ExperimentState, stage_model
from examples.search.experiment.slurm import SlurmSubmission
from examples.search.task.dataset import Candidate, DatasetConfig, Passage, parse_hotpot, partition
from examples.search.task.reward import answer_in_text, answer_reward, extract_answer


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


def test_training_steps_require_enough_full_batches() -> None:
    config = TrainingConfig(max_steps=200, epochs=1, batch_size=8)
    with pytest.raises(ValueError, match="allow 125 full updates"):
        config.validate_dataset_size(1000)
    replace(config, epochs=2).validate_dataset_size(1000)


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
    entrypoint = tmp_path / "examples/search/experiment/pipeline.py"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("# Frozen submission fixture.\n")
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
        assert entrypoint.is_file() and entrypoint.parent.name == "training"
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
        if "examples.search.task.retrieval" in args:
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


@pytest.mark.parametrize("hold_reason", ["JobHeldUser", "None"])
def test_heldout_submission_requests_two_gpus_and_refuses_changed_resume_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    hold_reason: str,
) -> None:
    config = search_config(tmp_path)
    path = tmp_path / "config.json"
    write_record(path, config)
    image = tmp_path / "image.sif"
    image.touch()
    submissions = []

    def submit(args: list[str], **kwargs: Any) -> str:
        if args[0] == "sbatch":
            assert {"--hold", "--reservation=rsi", "--gres=gpu:2"} <= set(args)
            submissions.append(args)
            return "123\n"
        assert args == ["scontrol", "show", "job", "-o", "123"]
        return f"JobId=123 JobState=PENDING Reason={hold_reason} Priority=0"

    def release(args: list[str], **kwargs: Any) -> int:
        assert args == ["scontrol", "release", "123"]
        receipt = read_record(
            Path(config.output_root) / "submissions/heldout/slurm-job.json", SlurmSubmission
        )
        assert receipt.release_pending  # The receipt exists before the job can start.
        return 0

    monkeypatch.setattr(subprocess, "check_output", submit)
    monkeypatch.setattr(subprocess, "check_call", release)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "search.pipeline",
            "--submit",
            "--config",
            str(path),
            "--run-id",
            "heldout",
            "--image",
            str(image),
            "--time-limit",
            "120",
            "--evaluate-only",
            "test",
        ],
    )
    pipeline.main()
    assert capsys.readouterr().out.strip() == "123"
    receipt = read_record(
        Path(config.output_root) / "submissions/heldout/slurm-job.json", SlurmSubmission
    )
    assert receipt.kind == "external-test-evaluation" and receipt.gpus == 2
    assert not receipt.release_pending
    assert slurm.submit(path, "heldout", image, 120, evaluation_split="test") == "123"
    assert len(submissions) == 1
    with pytest.raises(ValueError, match="different configuration or mode"):
        slurm.submit(path, "heldout", image, 120, evaluation_split="dev")
    with pytest.raises(ValueError, match="different configuration or mode"):
        slurm.submit(path, "heldout", image, 120)


@pytest.mark.parametrize("published", [False, True])
@pytest.mark.parametrize("cancel_fails", [False, True])
def test_failed_receipt_cancels_held_job_or_reports_its_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, published: bool, cancel_fails: bool
) -> None:
    config = search_config(tmp_path)
    path = tmp_path / "config.json"
    write_record(path, config)
    image = tmp_path / "image.sif"
    image.touch()
    submissions = []
    cancellations = []

    def submit(args: list[str], **kwargs: Any) -> str:
        assert args[0] == "sbatch" and "--hold" in args
        submissions.append(args)
        return "123\n"

    def fail_receipt(path: Path, value: Any) -> None:
        if path.name == "slurm-job.json":
            if published:
                write_record(path, value)
            raise OSError("injected receipt write failure")
        write_record(path, value)

    def cancel(args: list[str], **kwargs: Any) -> int:
        assert args == ["scancel", "123"]  # A failed write must never release the job.
        cancellations.append(args)
        if cancel_fails:
            raise OSError("injected cancellation failure")
        return 0

    monkeypatch.setattr(subprocess, "check_output", submit)
    monkeypatch.setattr(subprocess, "check_call", cancel)
    monkeypatch.setattr(slurm, "write_record", fail_receipt)
    message = "cancellation was not confirmed" if cancel_fails else "was cancelled"
    with pytest.raises(RuntimeError, match=f"Slurm job 123.*{message}"):
        slurm.submit(path, "candidate", image, 120)
    if not published:
        with pytest.raises(RuntimeError, match="Incomplete submission"):
            slurm.submit(path, "candidate", image, 120)
    assert len(submissions) == len(cancellations) == 1


@pytest.mark.parametrize(
    "failure",
    ["status_unavailable", "release_rejected", "release_ack_lost", "receipt_after_release"],
)
def test_submission_retry_recovers_release_without_submitting_another_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    config = search_config(tmp_path)
    path = tmp_path / "config.json"
    write_record(path, config)
    image = tmp_path / "image.sif"
    image.touch()
    receipt = Path(config.output_root) / "submissions/candidate/slurm-job.json"
    submissions = []
    releases = []
    released = False
    fault_raised = False

    def submit_or_status(args: list[str], **kwargs: Any) -> str:
        nonlocal fault_raised
        if args[0] == "sbatch":
            assert "--hold" in args
            submissions.append(args)
            return "123\n"
        assert args == ["scontrol", "show", "job", "-o", "123"]
        if failure == "status_unavailable" and not fault_raised:
            fault_raised = True
            raise OSError("injected status lookup failure")
        if released:
            return "JobId=123 JobState=RUNNING Reason=None"
        return "JobId=123 JobState=PENDING Reason=JobHeldUser"

    def release(args: list[str], **kwargs: Any) -> int:
        nonlocal released, fault_raised
        assert args == ["scontrol", "release", "123"]
        assert read_record(receipt, SlurmSubmission).release_pending
        releases.append(args)
        if failure == "release_rejected" and not fault_raised:
            fault_raised = True
            raise OSError("injected release failure")
        released = True
        if failure == "release_ack_lost" and not fault_raised:
            fault_raised = True
            raise OSError("injected release acknowledgement loss")
        return 0

    def save(path: Path, value: Any) -> None:
        nonlocal fault_raised
        if (
            failure == "receipt_after_release"
            and path == receipt
            and not value.release_pending
            and not fault_raised
        ):
            fault_raised = True
            raise OSError("injected acknowledgement write failure")
        write_record(path, value)

    monkeypatch.setattr(subprocess, "check_output", submit_or_status)
    monkeypatch.setattr(subprocess, "check_call", release)
    monkeypatch.setattr(slurm, "write_record", save)
    with pytest.raises(RuntimeError, match=r"Slurm job 123 is recorded.*release is not confirmed"):
        slurm.submit(path, "candidate", image, 120)
    assert read_record(receipt, SlurmSubmission).release_pending
    assert slurm.submit(path, "candidate", image, 120) == "123"
    assert not read_record(receipt, SlurmSubmission).release_pending
    assert released and len(submissions) == 1
    assert len(releases) == (2 if failure == "release_rejected" else 1)


def test_unobserved_submission_result_keeps_job_held_and_refuses_automatic_resubmission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = search_config(tmp_path)
    path = tmp_path / "config.json"
    write_record(path, config)
    image = tmp_path / "image.sif"
    image.touch()
    submissions = []

    def submit(args: list[str], **kwargs: Any) -> str:
        assert args[0] == "sbatch" and "--hold" in args
        submissions.append(args)
        raise OSError("Slurm accepted the held job but its response was lost")

    def release(args: list[str], **kwargs: Any) -> int:
        pytest.fail("An unrecorded job must never be released")

    monkeypatch.setattr(subprocess, "check_output", submit)
    monkeypatch.setattr(subprocess, "check_call", release)
    with pytest.raises(OSError, match="response was lost"):
        slurm.submit(path, "candidate", image, 120)
    with pytest.raises(RuntimeError, match=r"Incomplete submission.*search-candidate"):
        slurm.submit(path, "candidate", image, 120)
    assert len(submissions) == 1


def test_submitted_script_uses_frozen_source_when_calling_directory_shadows_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = search_config(tmp_path)
    marker = tmp_path / "frozen-entry-ran"
    entrypoint = tmp_path / "examples/search/experiment/pipeline.py"
    entrypoint.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('frozen')\n")
    # The test environment lacks CUDA; intercept only the CUDA-library lookup.
    interpreter = tmp_path / "image-python"
    interpreter.write_text(
        '#!/bin/sh\nif [ "$1" = "-c" ]; then printf "/tmp\\n"; exit 0; fi\nexec '
        + shlex.quote(sys.executable)
        + ' "$@"\n'
    )
    interpreter.chmod(0o700)
    config = replace(config, runtime=replace(config.runtime, python=str(interpreter)))
    config_path = tmp_path / "config.json"
    write_record(config_path, config)
    image = tmp_path / "image.sif"
    image.touch()
    monkeypatch.setattr(
        subprocess,
        "check_output",
        lambda args, **kwargs: (
            "123\n" if args[0] == "sbatch" else "JobId=123 JobState=PENDING Reason=JobHeldUser"
        ),
    )
    monkeypatch.setattr(subprocess, "check_call", lambda *args, **kwargs: 0)
    slurm.submit(config_path, "pinned-source", image, 120, evaluation_split="dev")
    shadow = tmp_path / "mutable-workspace"
    package = shadow / "examples/search/experiment"
    package.mkdir(parents=True)
    (shadow / "examples/__init__.py").touch()
    (shadow / "examples/search/__init__.py").touch()
    (package / "__init__.py").touch()
    (package / "pipeline.py").write_text("raise RuntimeError('mutable source was imported')\n")
    script = Path(config.output_root) / "submissions/pinned-source/inside.sh"
    subprocess.run(["bash", str(script)], cwd=shadow, check=True, capture_output=True, text=True)
    assert marker.read_text() == "frozen"
