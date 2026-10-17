"""Real subprocess lifecycles and artifact contracts, without simulated GPU results."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tarfile
from dataclasses import replace
from pathlib import Path

import pytest

from areal_rsi.api.control import encode_record
from areal_rsi.api.execution import AReaLRuntime, JobSpec, TrainingReceipt
from areal_rsi.api.records import EvaluationSample, TraceEvent, TrajectoryQuery
from areal_rsi.infra.artifacts import read_record, snapshot_source
from areal_rsi.infra.coordination.evaluation import EvaluationService
from areal_rsi.infra.execution import JobExecutionError, JobExecutor
from areal_rsi.infra.training import AReaLBackend
from areal_rsi.infra.trajectories import LocalTrajectoryStore
from examples.tir.data import SynthesisConfig, read_problems, synthesize, validate_problem
from examples.tir.pipeline import ExperimentConfig, ExperimentState, local_path, run
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
    config = ExperimentConfig(
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
    assert first.evaluation.digest == second.evaluation.digest
    larger = synthesize(SynthesisConfig(train_size=64), tmp_path / "larger")
    assert first.evaluation.digest == larger.evaluation.digest
    assert first.dev.digest == larger.dev.digest
    splits = [
        read_problems(tmp_path / "first" / f"{name}.jsonl") for name in ("train", "dev", "eval")
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


@pytest.mark.parametrize("task_ids", [("a", "b"), ("a", "a"), ("a", "c")])
def test_evaluation_requires_exact_task_coverage(
    tmp_path: Path,
    task_ids: tuple[str, ...],
) -> None:
    jobs = JobExecutor(tmp_path)
    traces = LocalTrajectoryStore(tmp_path / "traces")
    evaluator = EvaluationService(jobs, traces)
    directory = jobs.directory("run", "eval")
    sample = EvaluationSample("a", "answer", 1, True, (TraceEvent("generation", "answer"),))
    payload = "\n".join(
        json.dumps(encode_record(replace(sample, task_id=name))) for name in task_ids
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
