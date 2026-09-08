from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from areal_rsi.execution import ApprovedCommand, JobEvent, JobManager, JobRecord, JobSpec
from areal_rsi.execution._runner import process_identity, read_json, write_json


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


def test_local_job_logs_durable_result_and_no_score_from_stdout(tmp_path: Path) -> None:
    events: list[JobEvent] = []
    with JobManager(tmp_path, on_event=events.append, auto_monitor=False) as manager:
        job = manager.submit(python_job(tmp_path, "print('{\"score\": 999999}')"))
        result = wait_for(manager, job.job_id)
        assert result.state == "succeeded"
        assert result.exit_code == 0
        assert Path(result.log_path).read_text().strip() == '{"score": 999999}'
        assert "score" not in json.loads(Path(result.result_path).read_text())
        assert [event.state for event in events] == ["queued", "starting", "running", "succeeded"]
        assert len({event.event_id for event in events}) == len(events)
        count = len(events)
        manager.poll()
        assert len(events) == count
        assert Path(result.result_path).is_relative_to(tmp_path / "jobs")
        assert (Path(result.result_path).stat().st_mode & 0o777) == 0o600


def test_failure_and_missing_executable(tmp_path: Path) -> None:
    with JobManager(tmp_path, auto_monitor=False) as manager:
        failed = manager.submit(python_job(tmp_path, "raise SystemExit(17)"))
        assert wait_for(manager, failed.job_id).exit_code == 17
        missing = manager.submit(JobSpec(command=("/no-such-rsi-executable",), cwd=str(tmp_path)))
        record = wait_for(manager, missing.job_id)
        assert record.state == "failed"
        assert "FileNotFoundError" in (record.error or "")


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


def test_cpu_jobs_obey_concurrency_limit(tmp_path: Path) -> None:
    with JobManager(tmp_path, max_jobs=1, auto_monitor=False) as manager:
        first = manager.submit(JobSpec(backend="mock", mock_duration_seconds=0.3))
        second = manager.submit(JobSpec(backend="mock"))
        assert first.state == "running"
        assert second.state == "queued"
        wait_for(manager, first.job_id)
        assert wait_for(manager, second.job_id).state == "succeeded"


def test_unallocated_gpus_hidden_from_cpu_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0,1,2,3")
    with JobManager(tmp_path, gpu_ids=("0",), auto_monitor=False) as manager:
        job = manager.submit(
            python_job(tmp_path, 'import os; print(repr(os.environ["CUDA_VISIBLE_DEVICES"]))')
        )
        wait_for(manager, job.job_id)
        assert Path(job.log_path).read_text().strip() == "''"


def test_cancel_queued_job_never_launches(tmp_path: Path) -> None:
    with JobManager(tmp_path, auto_monitor=False) as manager:
        first = manager.submit(JobSpec(backend="mock", mock_duration_seconds=0.3))
        second = manager.submit(JobSpec(backend="mock"))
        assert manager.cancel(second.job_id).state == "cancelled"
        wait_for(manager, first.job_id)
        assert not (tmp_path / "jobs" / second.job_id / "started.lock").exists()


def test_cancel_terminates_child_process_group(tmp_path: Path) -> None:
    child_file = tmp_path / "child.pid"
    script = (
        "import pathlib,subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c',"
        "'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)']); "
        "pathlib.Path('child.pid').write_text(str(child.pid)); time.sleep(30)"
    )
    with JobManager(tmp_path, auto_monitor=False) as manager:
        job = manager.submit(python_job(tmp_path, script))
        wait_for_file(child_file)
        child_pid = int(child_file.read_text())
        assert process_identity(child_pid) is not None
        manager.cancel(job.job_id)
        assert wait_for(manager, job.job_id).state == "cancelled"
        deadline = time.monotonic() + 2
        while process_identity(child_pid) is not None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert process_identity(child_pid) is None


def test_timeout_is_enforced_when_service_is_closed(tmp_path: Path) -> None:
    manager = JobManager(tmp_path, auto_monitor=False)
    job = manager.submit(python_job(tmp_path, "import time; time.sleep(30)", timeout_seconds=0.2))
    manager.close(preserve_running=True)
    wait_for_file(Path(job.result_path))
    with JobManager(tmp_path, auto_monitor=False) as restarted:
        result = restarted.get(job.job_id)
        assert result.state == "timed_out"
        assert result.resources_released


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


def test_duplicate_submission_checks_spec_and_job_id(tmp_path: Path) -> None:
    with JobManager(tmp_path, auto_monitor=False) as manager:
        spec = JobSpec(backend="mock")
        job = manager.submit(spec, job_id="job-explicit1")
        assert manager.submit(spec, job_id="job-explicit1").job_id == job.job_id
        with pytest.raises(ValueError, match="different spec"):
            manager.submit(replace(spec, mock_exit_code=2), job_id="job-explicit1")
        with pytest.raises(ValueError, match="not both"):
            manager.submit(spec, job_id="job-explicit1", idempotency_key="other")
        wait_for(manager, job.job_id)


def test_restart_never_launches_queued_job_on_revoked_gpu(tmp_path: Path) -> None:
    manager = JobManager(tmp_path, gpu_ids=("0",), auto_monitor=False)
    first = manager.submit(JobSpec(backend="mock", gpu_count=1, mock_duration_seconds=0.3))
    queued = manager.submit(JobSpec(backend="mock", gpu_ids=("0",)))
    manager.close()
    wait_for_file(Path(first.result_path))
    with JobManager(tmp_path, gpu_ids=("1",), auto_monitor=False) as restarted:
        assert restarted.get(first.job_id).state == "succeeded"
        assert restarted.get(queued.job_id).state == "queued"
        restarted.cancel(queued.job_id)


def test_crashed_supervisor_is_not_restarted(tmp_path: Path) -> None:
    with JobManager(tmp_path, gpu_ids=("0",), auto_monitor=False) as manager:
        job = manager.submit(JobSpec(backend="mock", gpu_count=1, mock_duration_seconds=30))
        directory = tmp_path / "jobs" / job.job_id
        wait_for_file(directory / "handle.json")
        assert job.pid is not None
        os.kill(job.pid, signal.SIGKILL)
        deadline = time.monotonic() + 2
        while process_identity(job.pid) is not None and time.monotonic() < deadline:
            time.sleep(0.02)
        write_json(directory / "job.json", asdict(replace(manager.get(job.job_id), started_at=0.1)))
        manager.poll()
        lost = manager.get(job.job_id)
        assert lost.state == "lost"
        assert lost.pid == job.pid
        assert not lost.resources_released
        blocked = manager.submit(JobSpec(backend="mock", gpu_count=1))
        assert blocked.state == "queued"
        manager.cancel(blocked.job_id)


def test_stale_pid_identity_never_signals_unrelated_process(tmp_path: Path) -> None:
    with JobManager(tmp_path, auto_monitor=False) as manager:
        job = manager.submit(JobSpec(backend="mock", mock_duration_seconds=30))
        directory = tmp_path / "jobs" / job.job_id
        wait_for_file(directory / "handle.json")
        handle = read_json(directory / "handle.json")
        assert handle is not None
        write_json(
            directory / "handle.json",
            {
                **handle,
                "pid": os.getpid(),
                "process_identity": "stale birth time",
                "child_pid": os.getpid(),
                "child_identity": "stale birth time",
            },
        )
        write_json(directory / "job.json", asdict(replace(manager.get(job.job_id), started_at=0.1)))
        manager.poll()
        assert manager.get(job.job_id).state == "lost"
        assert not manager.get(job.job_id).resources_released
        assert process_identity(os.getpid()) is not None
        # Clean up the actual supervisor using its authenticated cancellation token.
        write_json(directory / "cancel.json", {"token": job.launch_token})
        wait_for_file(Path(job.result_path))
        manager.poll()
        assert manager.get(job.job_id).state == "cancelled"
        assert manager.get(job.job_id).resources_released


def test_orphan_command_is_stopped_without_reexecution(tmp_path: Path) -> None:
    with JobManager(tmp_path, auto_monitor=False) as manager:
        spec = python_job(
            tmp_path,
            "import pathlib,time; pathlib.Path('started').write_text('once'); time.sleep(30)",
        )
        job = manager.submit(spec, idempotency_key="orphan")
        directory = tmp_path / "jobs" / job.job_id
        wait_for_file(tmp_path / "started")
        deadline = time.monotonic() + 3
        handle = read_json(directory / "handle.json")
        while (handle is None or not handle.get("child_pid")) and time.monotonic() < deadline:
            time.sleep(0.02)
            handle = read_json(directory / "handle.json")
        assert handle is not None and handle["child_pid"] is not None
        assert job.pid is not None
        os.kill(job.pid, signal.SIGKILL)
        deadline = time.monotonic() + 2
        while process_identity(job.pid) is not None and time.monotonic() < deadline:
            time.sleep(0.02)
        write_json(directory / "job.json", asdict(replace(manager.get(job.job_id), started_at=0.1)))
        manager.poll()
        lost = manager.get(job.job_id)
        assert lost.state == "lost"
        assert process_identity(handle["child_pid"]) is None
        assert lost.resources_released
        assert manager.submit(spec, idempotency_key="orphan").state == "lost"
        assert (tmp_path / "started").read_text() == "once"


def test_notification_retry_survives_restart(tmp_path: Path) -> None:
    attempts: list[str] = []

    def failing(event: JobEvent) -> None:
        attempts.append(event.event_id)
        raise RuntimeError("consumer unavailable")

    manager = JobManager(tmp_path, on_event=failing, auto_monitor=False)
    job = manager.submit(JobSpec(backend="mock"))
    wait_for(manager, job.job_id)
    assert manager.callback_error is not None
    manager.close()
    delivered: list[JobEvent] = []
    with JobManager(tmp_path, on_event=delivered.append, auto_monitor=False) as restarted:
        assert delivered[0].event_id == attempts[0]
        assert delivered[-1].state == "succeeded"
        before = len(delivered)
        restarted.poll()
        assert len(delivered) == before


def test_monitor_advances_queue_without_agent_polling(tmp_path: Path) -> None:
    with JobManager(tmp_path) as manager:
        manager.submit(JobSpec(backend="mock", mock_duration_seconds=0.1))
        last = manager.submit(JobSpec(backend="mock"))
        deadline = time.monotonic() + 5
        while not manager.get(last.job_id).terminal and time.monotonic() < deadline:
            time.sleep(0.03)
        assert manager.get(last.job_id).state == "succeeded"


def test_only_one_manager_can_own_run_and_close_cancels(tmp_path: Path) -> None:
    manager = JobManager(tmp_path, auto_monitor=False)
    with pytest.raises(RuntimeError, match="Another job manager"):
        JobManager(tmp_path, auto_monitor=False)
    job = manager.submit(JobSpec(backend="mock", mock_duration_seconds=30))
    queued = manager.submit(JobSpec(backend="mock"))
    manager.close(preserve_running=False)
    with JobManager(tmp_path, auto_monitor=False) as restarted:
        assert restarted.get(job.job_id).state == "cancelled"
        assert restarted.get(queued.job_id).state == "cancelled"


@pytest.mark.parametrize(
    "spec",
    [
        JobSpec(),
        JobSpec(backend="mock", gpu_count=3),
        JobSpec(backend="mock", gpu_ids=("9",)),
        JobSpec(backend="mock", gpu_ids=("0", "0")),
        JobSpec(backend="mock", gpu_ids=("0",), gpu_count=2),
        JobSpec(backend="mock", timeout_seconds=0),
        JobSpec(backend="mock", timeout_seconds=float("nan")),
        JobSpec(backend="mock", mock_duration_seconds=-1),
    ],
)
def test_invalid_job_spec_is_rejected(tmp_path: Path, spec: JobSpec) -> None:
    with JobManager(tmp_path, gpu_ids=("0", "1"), auto_monitor=False) as manager:
        with pytest.raises(ValueError):
            manager.submit(spec)
        assert not manager.records()


def test_approved_template_binds_data_without_shell_interpretation(tmp_path: Path) -> None:
    approved = ApprovedCommand(
        "verify",
        (sys.executable, "-c", "import sys; print(sys.argv[1])", "{candidate}"),
        parameters=("candidate",),
    )
    malicious = "$(touch unexpected); `echo secret`"
    spec = approved.bind({"candidate": malicious}, cwd=str(tmp_path), iteration_id="node-1")
    assert spec.metadata == {"iteration_id": "node-1", "command_template": "verify"}
    with JobManager(tmp_path, auto_monitor=False) as manager:
        job = manager.submit(spec)
        assert wait_for(manager, job.job_id).state == "succeeded"
        assert Path(job.log_path).read_text().strip() == malicious
        assert not (tmp_path / "unexpected").exists()
    with pytest.raises(ValueError, match="expected parameters"):
        approved.bind({"command": "anything"}, cwd=str(tmp_path))
    with pytest.raises(ValueError, match="executable"):
        ApprovedCommand("bad", ("{program}",), parameters=("program",)).bind(
            {"program": "anything"}, cwd=str(tmp_path)
        )


def test_gpu_is_exclusive_across_runs_and_free_devices_can_progress(tmp_path: Path) -> None:
    with (
        JobManager(tmp_path / "runs/a", gpu_ids=("0",), auto_monitor=False) as first,
        JobManager(
            tmp_path / "runs/b", gpu_ids=("0", "1"), max_jobs=2, auto_monitor=False
        ) as second,
    ):
        owner = first.submit(JobSpec(backend="mock", gpu_count=1, mock_duration_seconds=0.5))
        blocked = second.submit(JobSpec(backend="mock", gpu_ids=("0",)))
        independent = second.submit(JobSpec(backend="mock", gpu_count=1))
        assert blocked.state == "queued"
        assert independent.assigned_gpu_ids == ("1",)
        assert wait_for(second, independent.job_id).state == "succeeded"
        completed = wait_for(first, owner.job_id)
        admitted = wait_for(second, blocked.job_id)
        assert admitted.state == "succeeded"
        assert admitted.started_at is not None and completed.finished_at is not None
        assert admitted.started_at >= completed.finished_at
        assert (tmp_path / "runtime/gpu-allocation.lock").read_bytes() == b""
        assert (tmp_path / "runs/a/jobs" / owner.job_id / "job.json").exists()


def test_multi_gpu_waiter_does_not_hold_partial_allocation(tmp_path: Path) -> None:
    with (
        JobManager(tmp_path / "runs/a", gpu_ids=("0", "1"), auto_monitor=False) as first,
        JobManager(tmp_path / "runs/b", gpu_ids=("1", "2"), auto_monitor=False) as second,
        JobManager(tmp_path / "runs/c", gpu_ids=("2",), auto_monitor=False) as third,
    ):
        owner = first.submit(JobSpec(backend="mock", gpu_count=2, mock_duration_seconds=0.4))
        waiting = second.submit(JobSpec(backend="mock", gpu_count=2))
        small = third.submit(JobSpec(backend="mock", gpu_count=1))
        assert waiting.state == "queued" and waiting.assigned_gpu_ids == ()
        assert small.state == "running" and small.assigned_gpu_ids == ("2",)
        wait_for(third, small.job_id)
        wait_for(first, owner.job_id)
        assert wait_for(second, waiting.job_id).assigned_gpu_ids == ("1", "2")


def test_offline_owner_keeps_gpu_until_durable_completion_then_other_run_can_use_it(
    tmp_path: Path,
) -> None:
    first = JobManager(tmp_path / "runs/a", gpu_ids=("0",), auto_monitor=False)
    owner = first.submit(JobSpec(backend="mock", gpu_count=1, mock_duration_seconds=0.4))
    first.close(preserve_running=True)
    with JobManager(tmp_path / "runs/b", gpu_ids=("0",), auto_monitor=False) as second:
        waiting = second.submit(JobSpec(backend="mock", gpu_count=1))
        assert waiting.state == "queued"
        wait_for_file(Path(owner.result_path))
        assert wait_for(second, waiting.job_id).state == "succeeded"
        # Reading foreign completion must not write the absent owner's job/event records.
        raw = read_json(tmp_path / "runs/a/jobs" / owner.job_id / "job.json")
        assert raw is not None and raw["state"] == "running"
    with JobManager(tmp_path / "runs/a", gpu_ids=("0",), auto_monitor=False) as restarted:
        assert restarted.get(owner.job_id).state == "succeeded"
        assert restarted.get(owner.job_id).pid == owner.pid


def test_foreign_stale_result_does_not_release_gpu(tmp_path: Path) -> None:
    with (
        JobManager(tmp_path / "runs/a", gpu_ids=("0",), auto_monitor=False) as first,
        JobManager(tmp_path / "runs/b", gpu_ids=("0",), auto_monitor=False) as second,
    ):
        owner = first.submit(JobSpec(backend="mock", gpu_count=1, mock_duration_seconds=30))
        write_json(
            Path(owner.result_path),
            {"token": "wrong-launch", "state": "succeeded", "resources_released": True},
        )
        waiting = second.submit(JobSpec(backend="mock", gpu_count=1))
        assert waiting.state == "queued"
        first.cancel(owner.job_id)
        assert wait_for(first, owner.job_id).state == "cancelled"
        assert wait_for(second, waiting.job_id).state == "succeeded"


def test_lost_gpu_claim_survives_owner_restart_and_blocks_other_runs(tmp_path: Path) -> None:
    first = JobManager(tmp_path / "runs/a", gpu_ids=("0",), auto_monitor=False)
    owner = first.submit(JobSpec(backend="mock", gpu_count=1, mock_duration_seconds=30))
    directory = tmp_path / "runs/a/jobs" / owner.job_id
    wait_for_file(directory / "handle.json")
    assert owner.pid is not None
    os.kill(owner.pid, signal.SIGKILL)
    deadline = time.monotonic() + 2
    while process_identity(owner.pid) is not None and time.monotonic() < deadline:
        time.sleep(0.02)
    write_json(directory / "job.json", asdict(replace(first.get(owner.job_id), started_at=0.1)))
    first.poll()
    assert first.get(owner.job_id).state == "lost"
    first.close()
    with (
        JobManager(tmp_path / "runs/a", gpu_ids=("0",), auto_monitor=False) as restarted,
        JobManager(tmp_path / "runs/b", gpu_ids=("0",), auto_monitor=False) as second,
    ):
        assert not restarted.get(owner.job_id).resources_released
        waiting = second.submit(JobSpec(backend="mock", gpu_count=1))
        assert waiting.state == "queued"
        second.cancel(waiting.job_id)


def test_lost_claim_releases_only_after_child_group_is_confirmed_stopped(tmp_path: Path) -> None:
    with (
        JobManager(tmp_path / "runs/a", gpu_ids=("0",), auto_monitor=False) as first,
        JobManager(tmp_path / "runs/b", gpu_ids=("0",), auto_monitor=False) as second,
    ):
        owner = first.submit(python_job(tmp_path, "import time; time.sleep(0.7)", gpu_count=1))
        directory = tmp_path / "runs/a/jobs" / owner.job_id
        wait_for_file(directory / "handle.json")
        handle = read_json(directory / "handle.json")
        deadline = time.monotonic() + 3
        while (handle is None or handle.get("child_pid") is None) and time.monotonic() < deadline:
            time.sleep(0.01)
            handle = read_json(directory / "handle.json")
        assert handle is not None and handle["child_pid"] is not None and owner.pid is not None
        # Simulate a missing birth-identity observation, so the live child may
        # not be signalled solely by its numeric PID.
        write_json(directory / "handle.json", {**handle, "child_identity": "unavailable"})
        os.kill(owner.pid, signal.SIGKILL)
        deadline = time.monotonic() + 2
        while process_identity(owner.pid) is not None and time.monotonic() < deadline:
            time.sleep(0.01)
        write_json(directory / "job.json", asdict(replace(first.get(owner.job_id), started_at=0.1)))
        first.poll()
        assert first.get(owner.job_id).state == "lost"
        assert not first.get(owner.job_id).resources_released
        waiting = second.submit(JobSpec(backend="mock", gpu_count=1))
        assert waiting.state == "queued"
        deadline = time.monotonic() + 3
        while process_identity(handle["child_pid"]) is not None and time.monotonic() < deadline:
            time.sleep(0.02)
        first.poll()
        assert first.get(owner.job_id).state == "lost"
        assert first.get(owner.job_id).resources_released
        assert wait_for(second, waiting.job_id).state == "succeeded"


def test_paused_scheduler_does_not_claim_gpu_from_another_run(tmp_path: Path) -> None:
    with (
        JobManager(
            tmp_path / "runs/a", gpu_ids=("0",), auto_monitor=False, scheduling_enabled=False
        ) as paused,
        JobManager(tmp_path / "runs/b", gpu_ids=("0",), auto_monitor=False) as active,
    ):
        waiting = paused.submit(JobSpec(backend="mock", gpu_count=1))
        owner = active.submit(JobSpec(backend="mock", gpu_count=1, mock_duration_seconds=0.3))
        assert waiting.state == "queued" and owner.state == "running"
        paused.scheduling_enabled = True
        paused.poll()
        assert paused.get(waiting.job_id).state == "queued"
        wait_for(active, owner.job_id)
        assert wait_for(paused, waiting.job_id).state == "succeeded"


def test_contended_global_lock_leaves_job_queued_without_blocking(tmp_path: Path) -> None:
    with JobManager(tmp_path / "runs/a", gpu_ids=("0",), auto_monitor=False) as manager:
        path = tmp_path / "runtime/gpu-allocation.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+") as held, ThreadPoolExecutor(max_workers=1) as pool:
            fcntl.flock(held, fcntl.LOCK_EX)
            pending = pool.submit(manager.submit, JobSpec(backend="mock", gpu_count=1))
            try:
                job = pending.result(timeout=1)
                assert job.state == "queued"
            finally:
                fcntl.flock(held, fcntl.LOCK_UN)
        assert wait_for(manager, job.job_id).state == "succeeded"


def test_independent_processes_cannot_allocate_the_same_gpu_concurrently(tmp_path: Path) -> None:
    script = """import json,sys,time
from pathlib import Path
from areal_rsi.execution import JobManager,JobSpec
root,ready,go=map(Path,sys.argv[1:])
with JobManager(root,gpu_ids=('0',),auto_monitor=False) as manager:
 ready.write_text('ready')
 deadline=time.monotonic()+10
 while not go.exists():
  if time.monotonic()>deadline: raise RuntimeError('barrier expired')
  time.sleep(0.01)
 job=manager.submit(JobSpec(backend='mock',gpu_count=1,mock_duration_seconds=0.4))
 while not manager.get(job.job_id).terminal:
  if time.monotonic()>deadline: raise RuntimeError('job expired')
  manager.poll();time.sleep(0.02)
 result=manager.get(job.job_id)
 print(json.dumps({'state':result.state,'start':result.started_at,'finish':result.finished_at}))
"""
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                script,
                str(tmp_path / "runs" / name),
                str(tmp_path / ("ready-" + name)),
                str(tmp_path / "go"),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for name in ("a", "b")
    ]
    try:
        for name in ("a", "b"):
            wait_for_file(tmp_path / ("ready-" + name))
        (tmp_path / "go").write_text("go")
        results = []
        for process in processes:
            output, error = process.communicate(timeout=12)
            assert process.returncode == 0, error
            results.append(json.loads(output))
        results.sort(key=lambda result: result["start"])
        assert all(result["state"] == "succeeded" for result in results)
        assert results[1]["start"] >= results[0]["finish"]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
