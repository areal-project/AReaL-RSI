"""Real scheduler-client subprocesses with a file-backed Slurm fixture, never a cluster."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from areal_rsi.api.execution import JobRecord, JobSpec, SlurmResources
from areal_rsi.infra import execution
from areal_rsi.infra.artifacts import read_record, write_record
from areal_rsi.infra.execution import JobExecutor


def scheduler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[JobExecutor, JobSpec]:
    clients = tmp_path / "clients"
    clients.mkdir()
    state = tmp_path / "scheduler"
    state.mkdir()
    monkeypatch.setenv("TEST_SLURM_STATE", str(state))
    monkeypatch.setenv("PATH", str(clients) + os.pathsep + os.environ["PATH"])
    script = (
        f"#!{sys.executable}\n"
        "import json,os,sys\nfrom pathlib import Path\n"
        "root=Path(os.environ['TEST_SLURM_STATE']); name=Path(sys.argv[0]).name\n"
        "with (root/'calls').open('a') as out: out.write(name+' '+' '.join(sys.argv[1:])+'\\n')\n"
        "if name=='sbatch':\n"
        " if (root/'max-walltime').exists():\n"
        "  requested=next(int(arg.split('=',1)[1]) "
        "for arg in sys.argv[1:] if arg.startswith('--time='))\n"
        "  assert requested<=int((root/'max-walltime').read_text()), "
        "'allocation exceeds scheduler window'\n"
        " (root/'state').write_text('held')\n"
        " if (root/'lost').exists(): sys.exit(1)\n"
        " print('123')\n"
        "elif name=='scontrol':\n"
        " if sys.argv[1]=='show':\n"
        "  if (root/'status-missing').exists(): sys.exit(1)\n"
        "  if (root/'status-failure').exists():\n"
        "   (root/'status-failure').unlink();sys.exit(1)\n"
        "  held=(root/'state').read_text()=='held'\n"
        "  print('JobId=123 JobState=PENDING Reason=JobHeldUser Priority=0' if held "
        "else 'JobId=123 JobState=RUNNING Reason=None')\n"
        " else:\n"
        "  receipt=json.loads(Path(os.environ['TEST_SLURM_RECEIPT']).read_text())\n"
        "  assert receipt['remote_id']=='123' and receipt['release_pending']\n"
        "  (root/'state').write_text('released')\n"
        "  if (root/'ack-lost').exists(): (root/'ack-lost').unlink();sys.exit(1)\n"
        "elif name=='sacct':\n"
        " if (root/'state').read_text()=='cancelled': print('123|CANCELLED|0:15')\n"
        " elif (root/'state').read_text()=='held': print('123|PENDING|0:0')\n"
        " elif (root/'active').exists():\n"
        "  status=(root/'active').read_text()\n"
        "  print('123|'+status+'|'+('0:15' if status=='TIMEOUT' else '0:0'))\n"
        " else: print('123|COMPLETED|0:0')\n"
        "elif name=='scancel': (root/'state').write_text('cancelled')\n"
    )
    for name in ("sbatch", "scontrol", "sacct", "scancel"):
        path = clients / name
        path.write_text(script)
        path.chmod(0o700)
    jobs = JobExecutor(tmp_path / "jobs")
    monkeypatch.setenv("TEST_SLURM_RECEIPT", str(jobs.directory("run", "stage") / "job.json"))
    return jobs, JobSpec(
        (sys.executable, "-c", "pass"),
        cwd=str(tmp_path),
        slurm=SlurmResources(gpus=2),
        timeout_seconds=10,
    )


@pytest.mark.parametrize(
    ("timeout", "requested_minutes", "expected_minutes"),
    [(70.5, 180, 2), (70.5, 1, 1), (None, 180, 180)],
)
def test_scheduler_accepts_allocation_within_software_and_explicit_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    timeout: float | None,
    requested_minutes: int,
    expected_minutes: int,
) -> None:
    jobs, spec = scheduler(tmp_path, monkeypatch)
    assert spec.slurm is not None
    (tmp_path / "scheduler/max-walltime").write_text(str(expected_minutes))
    spec = replace(
        spec,
        timeout_seconds=timeout,
        slurm=replace(spec.slurm, minutes=requested_minutes),
    )
    result = asyncio.run(jobs.run("run", "stage", spec))
    assert result.state == "succeeded" and result.resources_released
    calls = (tmp_path / "scheduler/calls").read_text()
    assert f"--time={expected_minutes} " in calls


@pytest.mark.parametrize("fault", ["status-failure", "ack-lost", "receipt-after-release"])
def test_release_recovery_keeps_exact_remote_job_and_persisted_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    jobs, spec = scheduler(tmp_path, monkeypatch)
    state = tmp_path / "scheduler"
    if fault != "receipt-after-release":
        (state / fault).touch()
    failed = False

    def save(path: Path, value: Any) -> Any:
        nonlocal failed
        if (
            fault == "receipt-after-release"
            and isinstance(value, JobRecord)
            and value.remote_id
            and not value.release_pending
            and not failed
        ):
            failed = True
            raise OSError("lost release receipt")
        return write_record(path, value)

    monkeypatch.setattr(execution, "write_record", save)
    with pytest.raises((RuntimeError, OSError)):
        asyncio.run(jobs.run("run", "stage", spec, recover=True))
    receipt = jobs.detail("run", "stage")
    assert receipt.remote_id == "123" and receipt.release_pending
    result = asyncio.run(jobs.run("run", "stage", spec, recover=True))
    assert result.state == "succeeded" and not result.release_pending
    assert asyncio.run(jobs.run("run", "stage", spec, recover=True)) == result
    calls = (state / "calls").read_text().splitlines()
    assert sum(line.startswith("sbatch ") for line in calls) == 1
    assert sum(line.startswith("scontrol release ") for line in calls) == 1
    with pytest.raises(ValueError, match="different plan"):
        asyncio.run(
            jobs.run("run", "stage", JobSpec(("different",), slurm=spec.slurm), recover=True)
        )


def test_unobserved_submission_remains_held_and_cannot_be_replayed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs, spec = scheduler(tmp_path, monkeypatch)
    state = tmp_path / "scheduler"
    (state / "lost").touch()
    with pytest.raises(RuntimeError):
        asyncio.run(jobs.run("run", "stage", spec, recover=True))
    with pytest.raises(RuntimeError, match="Unobserved Slurm submission"):
        asyncio.run(jobs.run("run", "stage", spec, recover=True))
    calls = (state / "calls").read_text().splitlines()
    assert len(calls) == 1 and (state / "state").read_text() == "held"


def test_release_recovery_accepts_accounting_after_job_leaves_scontrol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs, spec = scheduler(tmp_path, monkeypatch)
    state = tmp_path / "scheduler"
    (state / "ack-lost").touch()
    with pytest.raises(RuntimeError):
        asyncio.run(jobs.run("run", "stage", spec, recover=True))
    assert jobs.detail("run", "stage").release_pending
    (state / "status-missing").touch()
    result = asyncio.run(jobs.run("run", "stage", spec, recover=True))
    assert result.state == "succeeded" and result.resources_released
    assert not result.release_pending
    calls = (state / "calls").read_text().splitlines()
    assert sum(line.startswith("sbatch ") for line in calls) == 1
    assert sum(line.startswith("scontrol release ") for line in calls) == 1


def test_explicit_node_placement_is_passed_to_scheduler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs, spec = scheduler(tmp_path, monkeypatch)
    assert spec.slurm is not None
    resources = replace(spec.slurm, node="slurmd-7")
    spec = replace(spec, slurm=resources)
    result = asyncio.run(jobs.run("run", "stage", spec))
    assert result.state == "succeeded"
    calls = (tmp_path / "scheduler/calls").read_text()
    assert "--nodelist=slurmd-7" in calls
    script = (jobs.directory("run", "stage") / "run.sbatch").read_text()
    assert "export RSI_EXPECTED_GPUS=2" in script
    assert "export RSI_EXECUTION_DIRECTORY=" in script
    with pytest.raises(ValueError, match="one hostname"):
        replace(resources, node="slurmd-7 --exclusive")


def test_cancel_acknowledgment_can_reconnect_to_confirm_resource_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs, spec = scheduler(tmp_path, monkeypatch)
    state = tmp_path / "scheduler"
    (state / "status-failure").touch()
    with pytest.raises(RuntimeError):
        asyncio.run(jobs.run("run", "stage", spec, recover=True))
    cancelled = asyncio.run(jobs.cancel("run", "stage"))
    assert cancelled.remote_id == "123" and not cancelled.resources_released
    with pytest.raises(execution.JobExecutionError) as caught:
        asyncio.run(jobs.run("run", "stage", spec, recover=True))
    assert caught.value.record.resources_released
    assert caught.value.record.state == "cancelled"
    assert "CANCELLED" in (caught.value.record.error or "")
    calls = (state / "calls").read_text().splitlines()
    assert sum(line.startswith("sbatch ") for line in calls) == 1
    assert sum(line.startswith("scancel ") for line in calls) == 1
    assert sum(line.startswith("scontrol release ") for line in calls) == 0


@pytest.mark.parametrize("remote_state", ["PENDING", "RUNNING"])
def test_reconnecting_expired_job_keeps_deadline_and_confirms_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, remote_state: str
) -> None:
    jobs, spec = scheduler(tmp_path, monkeypatch)
    spec = replace(spec, timeout_seconds=3600)
    state = tmp_path / "scheduler"
    (state / ("status-failure" if remote_state == "PENDING" else "ack-lost")).touch()
    with pytest.raises(RuntimeError):
        asyncio.run(jobs.run("run", "stage", spec, recover=True))
    receipt = jobs.detail("run", "stage")
    write_record(
        jobs.directory("run", "stage") / "job.json",
        replace(receipt, created_at=time.time() - 7200),
    )
    (state / "active").write_text(remote_state)
    with pytest.raises(execution.JobExecutionError) as caught:
        asyncio.run(asyncio.wait_for(jobs.run("run", "stage", spec, recover=True), 5))
    result = caught.value.record
    assert result.remote_id == "123" and result.state == "timed_out"
    assert result.resources_released and "observation timeout" in (result.error or "")
    calls = (state / "calls").read_text().splitlines()
    assert sum(line.startswith("sbatch ") for line in calls) == 1
    assert sum(line.startswith("scancel ") for line in calls) == 1
    assert sum(line.startswith("scontrol release ") for line in calls) == (
        0 if remote_state == "PENDING" else 1
    )


def test_scheduler_time_limit_is_reported_as_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs, spec = scheduler(tmp_path, monkeypatch)
    (tmp_path / "scheduler/active").write_text("TIMEOUT")
    with pytest.raises(execution.JobExecutionError) as caught:
        asyncio.run(jobs.run("run", "stage", spec))
    assert caught.value.record.state == "timed_out"
    assert caught.value.record.resources_released
    assert "Slurm TIMEOUT" in (caught.value.record.error or "")


def test_failed_initial_receipt_cancels_only_the_new_held_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs, spec = scheduler(tmp_path, monkeypatch)

    def save(path: Path, value: Any) -> Any:
        if isinstance(value, JobRecord) and value.remote_id:
            raise OSError("receipt failed")
        return write_record(path, value)

    monkeypatch.setattr(execution, "write_record", save)
    with pytest.raises(OSError, match="receipt failed"):
        asyncio.run(jobs.run("run", "stage", spec, recover=True))
    calls = (tmp_path / "scheduler/calls").read_text()
    assert "scancel 123" in calls and "scontrol release" not in calls
    assert read_record(jobs.directory("run", "stage") / "job.json", JobRecord).remote_id is None
