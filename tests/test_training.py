"""Real subprocess lifecycles and artifact contracts, without simulated GPU results."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import tarfile
from dataclasses import replace
from pathlib import Path

import pytest

from areal_rsi.api.control import encode_record
from areal_rsi.api.execution import AReaLRuntime, JobSpec, TrainingReceipt
from areal_rsi.api.records import (
    BenchmarkSplit,
    EvaluationReport,
    EvaluationResult,
    EvaluationSample,
    TraceEvent,
    TrajectoryQuery,
)
from areal_rsi.infra.artifacts import file_ref, local_path, read_record, snapshot_source
from areal_rsi.infra.coordination.evaluation import (
    EvaluationService,
    paired,
    read_evaluation_samples,
)
from areal_rsi.infra.execution import JobExecutionError, JobExecutor
from areal_rsi.infra.training import AReaLBackend
from areal_rsi.infra.trajectories import LocalTrajectoryStore
from examples.tir.data import SynthesisConfig, read_problems, synthesize, validate_problem
from examples.tir.pipeline import ExperimentState, TIRConfig, run
from examples.tir.reward import integer_reward


def command(code: str, cwd: Path, *, timeout: float = 10) -> JobSpec:
    return JobSpec((sys.executable, "-c", code), cwd=str(cwd), timeout_seconds=timeout)


def git_repository(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )


def evaluation(
    path: Path,
    passed: tuple[bool, ...],
    *,
    task_ids: tuple[str, ...] | None = None,
    solution_id: str | None = None,
) -> EvaluationResult:
    path.parent.mkdir(parents=True, exist_ok=True)
    if task_ids is None:
        task_ids = tuple(str(index) for index in range(len(passed)))
    with path.open("x") as output:
        for task_id, success in zip(task_ids, passed, strict=True):
            sample = EvaluationSample(
                task_id, "test", float(success), success, (TraceEvent("generation", "test"),)
            )
            output.write(json.dumps(encode_record(sample)) + "\n")
    report = EvaluationReport(
        (solution_id or path.stem),
        BenchmarkSplit.VALIDATE,
        all(passed),
        sum(passed) / len(passed),
        {"accuracy": sum(passed) / len(passed), "num_tasks": float(len(passed))},
        "fixture",
    )
    return EvaluationResult("test", report, file_ref(path), ())


def test_prepare_isolated_runs_and_archives_dirty_source(tmp_path: Path) -> None:
    repository = tmp_path / "backend"
    (repository / "areal").mkdir(parents=True)
    (repository / "areal" / "__init__.py").touch()
    (repository / "examples" / "tir").mkdir(parents=True)
    (repository / "examples" / "tir" / "workflow.py").write_text("original = True\n")
    git_repository(repository)
    (repository / "examples" / "tir" / "workflow.py").write_text("original = False\n")
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text("{}")
    config = TIRConfig(
        AReaLRuntime(str(repository), sys.executable),
        str(model),
        output_root=str(tmp_path / "runs"),
    )
    first = asyncio.run(run(config, prepare_only=True))
    second = asyncio.run(run(config, prepare_only=True))
    assert first != second
    state = read_record(first / "experiment.json", ExperimentState)
    assert state.status == "prepared" and state.provenance is not None
    assert state.provenance.backend.dirty
    with tarfile.open(local_path(state.provenance.backend.archive)) as archive:
        content = archive.extractfile("examples/tir/workflow.py")
        assert content is not None and content.read() == b"original = False\n"
    original = state.provenance.backend.content.digest
    (repository / "examples" / "tir" / "workflow.py").write_text("another_change = True\n")
    changed = snapshot_source(repository, tmp_path / "changed.json", paths=("areal", "examples"))
    assert original != changed.content.digest
    data = local_path(state.provenance.datasets.train)
    data.write_text(data.read_text() + "\n")
    with pytest.raises(ValueError, match="changed"):
        local_path(state.provenance.datasets.train)


def test_synthesis_reproducible_verified_and_disjoint(tmp_path: Path) -> None:
    first = synthesize(SynthesisConfig(), tmp_path / "first")
    second = synthesize(SynthesisConfig(), tmp_path / "second")
    assert first.train.digest == second.train.digest
    assert first.test.digest == second.test.digest
    larger = synthesize(SynthesisConfig(train_size=64), tmp_path / "larger")
    assert first.test.digest == larger.test.digest
    assert first.valid.digest == larger.valid.digest
    splits = [
        read_problems(tmp_path / "first" / f"{name}.jsonl") for name in ("train", "valid", "test")
    ]
    identities = [{problem.task_id for problem in split} for split in splits]
    assert len(set.union(*identities)) == sum(len(split) for split in splits)
    with pytest.raises(ValueError, match="answer"):
        validate_problem(replace(splits[0][0], answer="wrong"))
    assert integer_reward("", r"Earlier \boxed{2}, final \boxed{1,234}", (), (), "1234") == 1
    assert integer_reward("", "1234 without the required answer format", (), (), "1234") == 0


def test_jobs_log_failure_and_refuse_to_overwrite(tmp_path: Path) -> None:
    jobs = JobExecutor(tmp_path)
    spec = command("print('evidence', flush=True); raise SystemExit(7)", tmp_path)
    with pytest.raises(JobExecutionError) as failure:
        asyncio.run(jobs.run("run", "failure", spec))
    assert failure.value.record.exit_code == 7
    assert jobs.detail("run", "failure").state == "failed"
    assert "evidence" in Path(failure.value.record.log_path).read_text()
    with pytest.raises(FileExistsError):
        asyncio.run(jobs.run("run", "failure", spec))
    with pytest.raises(JobExecutionError):
        asyncio.run(jobs.run("run", "missing", JobSpec(("/missing/executable",))))
    assert jobs.detail("run", "missing").resources_released


def test_timeout_and_cancellation_kill_descendants(tmp_path: Path) -> None:
    jobs = JobExecutor(tmp_path)
    pid_file = tmp_path / "child.pid"
    code = (
        "import subprocess,sys,time; from pathlib import Path; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'], "
        "start_new_session=True); "
        f"Path({str(pid_file)!r}).write_text(str(child.pid)); time.sleep(60)"
    )
    with pytest.raises(JobExecutionError):
        asyncio.run(jobs.run("run", "timeout", command(code, tmp_path, timeout=0.5)))
    assert jobs.detail("run", "timeout").state == "timed_out"
    child = int(pid_file.read_text())
    status = Path(f"/proc/{child}/status")
    assert not status.exists() or "State:\tZ" in status.read_text()

    async def cancel() -> None:
        task = asyncio.create_task(jobs.run("run", "cancel", command(code, tmp_path)))
        while not (jobs.directory("run", "cancel") / "job.json").exists():
            await asyncio.sleep(0.01)
        while jobs.detail("run", "cancel").state != "running":
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel())
    record = jobs.detail("run", "cancel")
    assert record.state == "cancelled" and record.resources_released
    assert record.pid is not None
    with pytest.raises(ProcessLookupError):
        os.kill(record.pid, 0)


def test_surviving_descendant_is_not_reported_as_released(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs = JobExecutor(tmp_path)
    pid_file = tmp_path / "survivor.pid"
    real_kill = os.kill
    child_pid: int | None = None

    def ignore_child_kill(pid: int, sig: int) -> None:
        if pid_file.exists() and pid == int(pid_file.read_text()) and sig == signal.SIGKILL:
            return
        real_kill(pid, sig)

    monkeypatch.setattr(os, "kill", ignore_child_kill)
    code = (
        "import subprocess,sys,time; from pathlib import Path; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'], "
        "start_new_session=True); "
        f"Path({str(pid_file)!r}).write_text(str(child.pid)); time.sleep(60)"
    )
    try:
        with pytest.raises(JobExecutionError) as failed:
            asyncio.run(jobs.run("run", "survivor", command(code, tmp_path, timeout=0.5)))
        child_pid = int(pid_file.read_text())
        assert failed.value.record.state == "timed_out"
        assert not failed.value.record.resources_released
        assert "cleanup incomplete" in (failed.value.record.error or "")
        assert not jobs.detail("run", "survivor").resources_released
    finally:
        if child_pid is None and pid_file.exists():
            child_pid = int(pid_file.read_text())
        if child_pid is not None:
            real_kill(child_pid, signal.SIGKILL)


@pytest.mark.parametrize("task_ids", [("a", "b"), ("a", "a"), ("a", "c")])
def test_evaluation_requires_exact_task_coverage(
    tmp_path: Path,
    task_ids: tuple[str, ...],
) -> None:
    jobs = JobExecutor(tmp_path)
    traces = LocalTrajectoryStore(tmp_path / "traces")
    evaluator = EvaluationService(jobs, traces)
    directory = jobs.directory("run", "eval")
    sample = EvaluationSample(
        "a", "answer\u2028line", 1, True, (TraceEvent("generation", "answer\u2028line"),)
    )
    payload = "\n".join(
        json.dumps(encode_record(replace(sample, task_id=name)), ensure_ascii=False)
        for name in task_ids
    )
    code = (
        "from pathlib import Path; "
        f"Path({str(directory / 'samples.jsonl')!r}).write_text({payload!r})"
    )
    operation = evaluator.evaluate(
        "run", "eval", command(code, tmp_path), task_ids=("a", "b"), solution_id="solution"
    )
    if task_ids != ("a", "b"):
        with pytest.raises(ValueError, match="Incomplete evaluation"):
            asyncio.run(operation)
        assert not (directory / "evaluation.json").exists()
        return
    result = asyncio.run(operation)
    assert result.report.score == 1 and result.report.metrics["num_tasks"] == 2
    first = asyncio.run(traces.query(TrajectoryQuery(limit=1)))
    assert first.next_cursor is not None
    second = asyncio.run(traces.query(TrajectoryQuery(limit=1, cursor=first.next_cursor)))
    assert first.trajectories != second.trajectories
    original = asyncio.run(traces.get(first.trajectories[0].trajectory_id))
    assert original is not None
    assert asyncio.run(traces.save(original)) == first.trajectories[0]
    with pytest.raises(ValueError, match="different content"):
        asyncio.run(traces.save(replace(original, task_id="tampered")))
    queried = asyncio.run(traces.query(TrajectoryQuery(task_id="a", solution_id="solution")))
    assert len(queried.trajectories) == 1


@pytest.mark.parametrize("condition", ["valid", "outside", "missing_shard", "no_steps"])
def test_training_rejects_stale_or_incomplete_checkpoint(
    tmp_path: Path,
    condition: str,
) -> None:
    repository = tmp_path / "backend"
    (repository / "areal").mkdir(parents=True)
    (repository / "areal" / "__init__.py").touch()
    jobs = JobExecutor(tmp_path)
    backend = AReaLBackend(AReaLRuntime(str(repository), sys.executable), jobs)
    output = jobs.directory("run", "train")
    checkpoint = output / "checkpoint" if condition != "outside" else tmp_path / "old"
    receipt = TrainingReceipt(str(checkpoint), 0 if condition == "no_steps" else 1)
    receipt_json = json.dumps(encode_record(receipt))
    code = (
        "from pathlib import Path; "
        f"root=Path({str(checkpoint)!r}); root.mkdir(parents=True); "
        "(root/'config.json').write_text('{}'); "
        "(root/'model.safetensors').write_bytes(b'test contract artifact, not a trained model'); "
        f"Path({str(output / 'training.json')!r}).write_text({receipt_json!r})"
    )
    if condition == "missing_shard":
        index = json.dumps({"weight_map": {"weight": "missing.safetensors"}})
        code += f"; (root/'model.safetensors.index.json').write_text({index!r})"
    operation = backend.train("run", "train", command(code, tmp_path))
    if condition == "valid":
        result = asyncio.run(operation)
        assert result.global_step == 1
        assert (output / "checkpoint.json").exists()
        assert read_record(output / "training.json", TrainingReceipt) == receipt
    else:
        with pytest.raises(ValueError):
            asyncio.run(operation)
        assert not (output / "result.json").exists()


def test_paired_statistics_align_tasks_and_refuse_tampering(tmp_path: Path) -> None:
    baseline = evaluation(tmp_path / "base.jsonl", (False, False, False, False, True, True))
    candidate = evaluation(tmp_path / "candidate.jsonl", (True, True, True, True, False, True))
    result = paired(baseline, candidate)
    assert (result.wins, result.losses, result.both_correct, result.both_wrong) == (4, 1, 1, 0)
    assert result.delta == 0.5 and result.mcnemar_exact_p == 0.375
    assert result.ci95_low <= result.delta <= result.ci95_high
    shorter = evaluation(tmp_path / "shorter.jsonl", (True, False))
    with pytest.raises(ValueError, match="same tasks"):
        paired(baseline, shorter)
    Path(tmp_path / "candidate.jsonl").write_text("tampered")
    with pytest.raises(ValueError, match="changed"):
        paired(baseline, candidate)


@pytest.mark.parametrize("mismatch", ["score", "metrics", "solution", "split", "tasks"])
def test_cached_evaluation_rejects_inconsistent_evidence(tmp_path: Path, mismatch: str) -> None:
    result = evaluation(
        tmp_path / "samples.jsonl", (False, True), task_ids=("a", "b"), solution_id="model"
    )
    assert len(read_evaluation_samples(result)) == 2
    expected_tasks = ("a", "b")
    report = result.report
    if mismatch == "score":
        report = replace(report, score=1)
    elif mismatch == "metrics":
        report = replace(report, metrics={**report.metrics, "accuracy": 1})
    elif mismatch == "solution":
        report = replace(report, solution_id="another-model")
    elif mismatch == "split":
        report = replace(report, split=BenchmarkSplit.TEST)
    else:
        expected_tasks = ("a", "c")
    with pytest.raises(ValueError):
        read_evaluation_samples(
            replace(result, report=report),
            task_ids=expected_tasks,
            solution_id="model",
            split=BenchmarkSplit.VALIDATE,
        )


def test_repeated_samples_do_not_inflate_independent_question_evidence(tmp_path: Path) -> None:
    base = evaluation(tmp_path / "base.jsonl", (False,) * 10)
    trained = evaluation(tmp_path / "trained.jsonl", (True,) * 8 + (False,) * 2)
    clusters = {str(index): "repeated" if index < 8 else str(index) for index in range(10)}
    clustered = paired(base, trained, clusters=clusters)
    assert clustered.count == 10 and clustered.independent_questions == 3
    assert clustered.delta == 0.8 and clustered.mcnemar_exact_p is None
    assert clustered.ci95_low == 0
    assert paired(base, trained).ci95_low > 0
    with pytest.raises(ValueError, match="clusters must cover"):
        paired(base, trained, clusters={"0": "only-one"})
