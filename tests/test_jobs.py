from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

import areal_rsi.execution.manager as manager_module
from areal_rsi.execution import JobManager, JobRecord, JobSpec


def wait_for(manager: JobManager, job_id: str, *, timeout: float = 8) -> JobRecord:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        manager.poll()
        record = manager.get(job_id)
        if record.terminal:
            return record
        time.sleep(0.03)
    raise AssertionError(f"Job did not finish: {manager.get(job_id)}")


def wait_for_file(path: Path, *, timeout: float = 8) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.03)
    assert path.exists(), path


def python_job(root: Path, script: str, **kwargs: object) -> JobSpec:
    return JobSpec(command=(sys.executable, "-u", "-c", script), cwd=str(root), **kwargs)  # type: ignore[arg-type]


def test_gpu_queue_allocations_and_explicit_ids(tmp_path: Path) -> None:
    with JobManager(tmp_path, gpu_ids=("4", "7"), max_jobs=3, auto_monitor=False) as manager:
        first = manager.submit(JobSpec(backend="mock", gpu_ids=("4",), mock_duration_seconds=0.3))
        blocked = manager.submit(JobSpec(backend="mock", gpu_count=2))
        second = manager.submit(
            python_job(
                tmp_path, 'import os; print(os.environ["CUDA_VISIBLE_DEVICES"])', gpu_count=1
            )
        )
        assert first.assigned_gpu_ids == ("4",)
        assert blocked.state == "queued"
        assert second.assigned_gpu_ids == ("7",)
        assert wait_for(manager, second.job_id).state == "succeeded"
        assert Path(second.log_path).read_text().strip() == "7"
        assert wait_for(manager, first.job_id).state == "succeeded"
        final = wait_for(manager, blocked.job_id)
        assert final.state == "succeeded"
        assert final.assigned_gpu_ids == ("4", "7")
        assert json.loads(Path(final.result_path).read_text())["backend"] == "mock"


def test_restart_reattaches_running_job_without_duplicate_execution(tmp_path: Path) -> None:
    marker = tmp_path / "executions.txt"
    manager = JobManager(tmp_path, auto_monitor=False)
    spec = python_job(
        tmp_path,
        "import pathlib,time; p=pathlib.Path('executions.txt'); "
        "p.write_text(p.read_text()+'x' if p.exists() else 'x'); time.sleep(0.7)",
    )
    job = manager.submit(spec, idempotency_key="tool-call-1")
    wait_for_file(marker)
    manager.close()
    with JobManager(tmp_path, auto_monitor=False) as restarted:
        same = restarted.submit(spec, idempotency_key="tool-call-1")
        assert same.job_id == job.job_id
        assert same.pid == job.pid
        assert wait_for(restarted, job.job_id).state == "succeeded"
        assert marker.read_text() == "x"


def test_transient_identity_failure_does_not_lose_live_supervisor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = JobManager(tmp_path, auto_monitor=False)
    job = manager.submit(python_job(tmp_path, "import time; time.sleep(2)"))
    wait_for_file(tmp_path / "jobs" / job.job_id / "handle.json")
    with monkeypatch.context() as patch:
        patch.setattr(manager_module, "process_identity", lambda _pid: None)
        patch.setattr(manager_module, "group_exists", lambda _pid: True)
        manager.poll()
        assert manager.get(job.job_id).state == "running"
    manager.close(preserve_running=False)


def test_schedule_filter_keeps_ineligible_job_queued(tmp_path: Path) -> None:
    eligible: set[str] = set()
    with JobManager(
        tmp_path,
        max_jobs=1,
        auto_monitor=False,
        schedule_filter=lambda record: record.job_id in eligible,
    ) as manager:
        job = manager.submit(
            JobSpec(backend="mock", mock_duration_seconds=0), job_id="job-filtered"
        )
        assert job.state == "queued"
        eligible.add(job.job_id)
        assert wait_for(manager, job.job_id).state == "succeeded"
