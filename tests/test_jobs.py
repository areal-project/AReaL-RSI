from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

import pytest

from areal_rsi.infra.execution import JobManager, JobRecord, JobSpec, _runner


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


@pytest.mark.parametrize("descendants_alive", [False, True])
def test_supervisor_retains_claim_until_entire_group_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, descendants_alive: bool
) -> None:
    class ExitedCommand:
        pid = 12345

        def poll(self) -> int:
            return 0

        def wait(self, *, timeout: float) -> int:
            return 0

    monkeypatch.setattr("areal_rsi.infra.execution._runner.signal.signal", lambda *args: None)
    monkeypatch.setattr(_runner, "process_identity", lambda pid: "fixture")
    monkeypatch.setattr(
        "areal_rsi.infra.execution._runner.subprocess.Popen",
        lambda *args, **kwargs: ExitedCommand()
    )
    monkeypatch.setattr(_runner, "terminate_group", lambda pid: None)
    monkeypatch.setattr(_runner, "group_exists", lambda pid: descendants_alive)
    _runner.write_json(tmp_path / "launch.json", {
        "token": "launch", "spec": asdict(JobSpec(command=("fixture",))),
        "assigned_gpu_ids": ["0"],
    })
    assert _runner.run(tmp_path, "launch") == 0
    result = _runner.read_json(tmp_path / "result.json")
    assert result is not None
    assert result["resources_released"] is not descendants_alive
    assert result["state"] == ("lost" if descendants_alive else "succeeded")
