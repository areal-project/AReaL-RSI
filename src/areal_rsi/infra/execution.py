"""Durable local and Slurm receipts shared by evaluation and training jobs.

Slurm owns allocation scheduling; AReaL owns its workers within an allocation.
Local jobs use the caller's existing resources.
"""

from __future__ import annotations

import asyncio
import csv
import math
import os
import re
import shlex
import signal
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path

from areal_rsi.api.execution import GPUAllocation, GPUDevice, JobRecord, JobSpec, JobState
from areal_rsi.infra.artifacts import read_record, write_record


class JobExecutionError(RuntimeError):
    def __init__(self, record: JobRecord):
        self.record = record
        super().__init__(
            f"Job {record.job_id}: {record.state}; {record.error}; log: {record.log_path}"
        )


def verify_gpu_allocation() -> GPUAllocation | None:
    """Record assigned devices and reject occupied GPUs before loading any model."""
    job_id = os.environ.get("SLURM_JOB_ID")
    if not job_id:
        return None
    visible = tuple(filter(None, os.environ.get("CUDA_VISIBLE_DEVICES", "").split(",")))
    expected = int(os.environ.get("RSI_EXPECTED_GPUS", str(len(visible))))
    devices: tuple[GPUDevice, ...] = ()
    if expected:
        if len(visible) != expected:
            raise RuntimeError("CUDA visibility does not match the requested Slurm GPU count")
        output = subprocess.check_output(
            [
                "nvidia-smi",
                "--id=" + ",".join(visible),
                "--query-gpu=index,uuid,name,memory.total,memory.used",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=15,
        )
        devices = tuple(
            GPUDevice(int(index), uuid.strip(), name.strip(), int(total), int(used))
            for index, uuid, name, total, used in csv.reader(output.splitlines())
        )
        actual = {str(device.index) for device in devices} | {device.uuid for device in devices}
        if (
            len(devices) != expected
            or len({device.uuid for device in devices}) != expected
            or (set(visible) - actual)
        ):
            raise RuntimeError("GPU inventory does not match the assigned CUDA devices")
    allocation = GPUAllocation(job_id, os.environ["SLURMD_NODENAME"], devices)
    directory = Path(os.environ["RSI_EXECUTION_DIRECTORY"])
    path = directory / "gpu-allocation.json"
    if path.exists():
        raise FileExistsError(f"GPU admission receipt already exists: {path}")
    write_record(path, allocation)
    # Permit driver/context overhead, but not another workload's model allocations.
    occupied = [device for device in devices if device.used_memory_mib > 1024]
    if occupied:
        detail = ", ".join(f"{d.index}: {d.used_memory_mib} MiB" for d in occupied)
        raise RuntimeError(f"Assigned GPUs are occupied before workload launch ({detail})")
    return allocation


async def _scheduler_command(*arguments: str) -> str:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"RSI_API_KEY", "OPENAI_API_KEY", "SEARCH_PROXY_ADMIN_KEY"}
    }
    process = await asyncio.create_subprocess_exec(
        *arguments,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    output, error = await process.communicate()
    if process.returncode:
        raise RuntimeError(f"{arguments[0]}: {error.decode().strip()}")
    return output.decode().strip()


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    pid: int
    started: str

    @classmethod
    def read(cls, pid: int) -> ProcessIdentity | None:
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
        except FileNotFoundError:
            if not Path("/proc").exists():
                result = subprocess.run(
                    ["ps", "-p", str(pid), "-o", "lstart=", "-o", "stat="],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                fields = result.stdout.strip().rsplit(None, 1)
                if len(fields) != 2 or fields[1].startswith(("Z", "X")):
                    return None
                return cls(pid, fields[0])
            return None
        fields = stat.rsplit(")", 1)[1].split()
        return None if fields[0] in {"Z", "X"} else cls(pid, fields[19])


def terminate_process_tree(pid: int) -> bool:
    """Release an owned session and its observed descendants, including detached ones."""
    descendants: list[ProcessIdentity] = []
    table = subprocess.check_output(["ps", "-axo", "pid=,ppid="], text=True)
    relations = [tuple(map(int, line.split())) for line in table.splitlines()]
    parents = [pid]
    while parents:
        children = [child for child, parent in relations if parent in parents]
        descendants.extend(
            identity for child in children if (identity := ProcessIdentity.read(child)) is not None
        )
        parents = children
    parent = ProcessIdentity.read(pid)
    with suppress(ProcessLookupError):
        os.killpg(pid, signal.SIGTERM)
    deadline = time.monotonic() + 5
    while parent is not None and ProcessIdentity.read(pid) == parent:
        if time.monotonic() >= deadline:
            break
        time.sleep(0.02)
    with suppress(ProcessLookupError):
        os.killpg(pid, signal.SIGKILL)
    for identity in reversed(descendants):
        if ProcessIdentity.read(identity.pid) == identity:
            with suppress(ProcessLookupError):
                os.kill(identity.pid, signal.SIGKILL)
    deadline = time.monotonic() + 5
    identities = ([parent] if parent is not None else []) + descendants
    while any(ProcessIdentity.read(item.pid) == item for item in identities):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)
    return True


class JobExecutor:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def directory(self, run_id: str, name: str) -> Path:
        if any(not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", item) for item in (run_id, name)):
            raise ValueError("Invalid run or job name")
        return self.root / run_id / "jobs" / name

    def detail(self, run_id: str, name: str) -> JobRecord:
        return read_record(self.directory(run_id, name) / "job.json", JobRecord)

    async def run(
        self, run_id: str, name: str, spec: JobSpec, *, recover: bool = False
    ) -> JobRecord:
        if spec.backend != "local" or not spec.command:
            raise ValueError("A real executable command is required")
        if spec.timeout_seconds is not None and (
            not math.isfinite(spec.timeout_seconds) or spec.timeout_seconds <= 0
        ):
            raise ValueError("Job timeout must be positive")
        directory = self.directory(run_id, name)
        if recover and directory.exists():
            record = self.detail(run_id, name)
            if record.spec != spec:
                raise ValueError("Existing execution has a different plan")
            if record.state == "succeeded" and record.resources_released:
                return record
            if spec.slurm is not None and (not record.terminal or not record.resources_released):
                return await self._slurm(directory, record)
            raise JobExecutionError(record)
        directory.mkdir(parents=True, exist_ok=False)
        now = time.time()
        record = JobRecord(
            f"{run_id}:{name}",
            spec,
            "starting",
            now,
            now,
            log_path=str(directory / "stdout.log"),
            result_path=str(directory),
        )

        def save() -> None:
            write_record(directory / "job.json", record)

        save()
        if spec.slurm is not None:
            return await self._slurm(directory, record)
        process: asyncio.subprocess.Process | None = None
        try:
            with Path(record.log_path).open("wb") as log:
                spawning = asyncio.create_task(
                    asyncio.create_subprocess_exec(
                        *spec.command,
                        cwd=spec.cwd,
                        env={**os.environ, **spec.env},
                        stdout=log,
                        stderr=asyncio.subprocess.STDOUT,
                        start_new_session=True,
                    )
                )
                try:
                    process = await asyncio.shield(spawning)
                except asyncio.CancelledError:
                    process = await spawning
                    raise
                record = replace(
                    record,
                    state="running",
                    pid=process.pid,
                    process_identity=(
                        identity.started
                        if (identity := ProcessIdentity.read(process.pid)) is not None
                        else None
                    ),
                    started_at=time.time(),
                    resources_released=False,
                )
                save()
                try:
                    code = await asyncio.wait_for(process.wait(), spec.timeout_seconds)
                    record = replace(
                        record,
                        state="succeeded" if code == 0 else "failed",
                        exit_code=code,
                        error=None if code == 0 else f"Exit code {code}",
                    )
                except TimeoutError:
                    record = replace(record, state="timed_out", error="Execution timeout")
        except asyncio.CancelledError:
            record = replace(record, state="cancelled", error="Caller cancelled execution")
            raise
        except OSError as exc:
            record = replace(record, state="failed", error=str(exc))
        finally:
            released = True
            if process is not None:
                released = await self._terminate(process)
                record = replace(record, exit_code=process.returncode)
                if not released:
                    record = replace(
                        record,
                        state="failed" if record.state == "succeeded" else record.state,
                        error=(
                            f"{record.error or 'Execution ended'}; process tree cleanup incomplete"
                        ),
                    )
            record = replace(
                record, updated_at=time.time(), finished_at=time.time(), resources_released=released
            )
            save()
        if record.state != "succeeded":
            raise JobExecutionError(record)
        return record

    async def _slurm(self, directory: Path, record: JobRecord) -> JobRecord:
        """Persist a held submission before release; never replay an ambiguous sbatch."""
        spec, path = record.spec, directory / "job.json"
        resources = spec.slurm
        assert resources is not None
        minutes = resources.minutes
        if spec.timeout_seconds is not None:
            minutes = min(minutes, max(1, math.ceil(spec.timeout_seconds / 60)))
        script = directory / "run.sbatch"

        try:
            if record.remote_id is None:
                # The marker survives a lost response. Recovery requires operator reconciliation.
                if script.exists():
                    raise RuntimeError(
                        f"Unobserved Slurm submission at {directory}; inspect job name "
                        f"{record.job_id.replace(':', '-')} before retrying"
                    )
                script.write_text(
                    "#!/usr/bin/env bash\nset -euo pipefail\n"
                    'export RSI_SLURM_JOB_INFO="$(scontrol show job -o "$SLURM_JOB_ID")"\n'
                    + "cd "
                    + shlex.quote(spec.cwd)
                    + "\n"
                    + "".join(
                        "export " + key + "=" + shlex.quote(value) + "\n"
                        for key, value in spec.env.items()
                        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
                    )
                    + f"export RSI_EXPECTED_GPUS={resources.gpus}\n"
                    + "export RSI_EXECUTION_DIRECTORY="
                    + shlex.quote(str(directory))
                    + "\n"
                    + "exec "
                    + shlex.join(spec.command)
                    + "\n"
                )
                submitted = await _scheduler_command(
                    "sbatch",
                    "--parsable",
                    "--hold",
                    f"--reservation={resources.reservation}",
                    f"--partition={resources.partition}",
                    "--nodes=1",
                    "--ntasks=1",
                    f"--gres=gpu:{resources.gpus}",
                    f"--cpus-per-task={resources.cpus}",
                    f"--mem={resources.memory_gb}G",
                    f"--time={minutes}",
                    *((f"--nodelist={resources.node}",) if resources.node else ()),
                    "--job-name=" + record.job_id.replace(":", "-"),
                    "--output=" + record.log_path,
                    str(script),
                )
                remote_id = submitted.split(";")[0]
                if not remote_id.isdigit():
                    raise RuntimeError("Invalid Slurm submission response; reconcile the job")
                record = replace(
                    record, remote_id=remote_id, release_pending=True, resources_released=False
                )
                try:
                    write_record(path, record)
                except BaseException:
                    try:
                        await _scheduler_command("scancel", remote_id)
                    except Exception as exc:
                        raise RuntimeError(
                            f"Slurm job {remote_id} is held, receipt failed and cancellation "
                            f"was not confirmed; inspect {directory}"
                        ) from exc
                    raise
            assert record.remote_id is not None
            remote_id = record.remote_id
            deadline = record.created_at + spec.timeout_seconds if spec.timeout_seconds else None
            release_error: RuntimeError | None = None
            if (
                record.release_pending
                and not record.terminal
                and (deadline is None or time.time() < deadline)
            ):
                try:
                    status = set(
                        (
                            await _scheduler_command("scontrol", "show", "job", "-o", remote_id)
                        ).split()
                    )
                except RuntimeError as exc:
                    # Completed jobs can leave scontrol before recovery. Only a terminal
                    # accounting record may resolve the outstanding release receipt.
                    release_error = exc
                else:
                    if (
                        f"JobId={remote_id}" not in status
                        or "Reason=JobHeldAdmin" in status
                        or not any(item.startswith("JobState=") for item in status)
                        or not any(item.startswith("Reason=") for item in status)
                    ):
                        raise RuntimeError("Cannot confirm the submitted Slurm job's release state")
                    if "Reason=JobHeldUser" in status or (
                        "JobState=PENDING" in status and "Priority=0" in status
                    ):
                        await _scheduler_command("scontrol", "release", remote_id)
                    record = replace(record, release_pending=False)
                    write_record(path, record)
            while True:
                rows = await _scheduler_command(
                    "sacct",
                    "-j",
                    remote_id,
                    "-X",
                    "-n",
                    "-P",
                    "--format=JobIDRaw,State,ExitCode",
                )
                row = next(
                    (
                        line.split("|")
                        for line in rows.splitlines()
                        if line.split("|")[0] == remote_id
                    ),
                    None,
                )
                if row and len(row) >= 3:
                    state = row[1].split()[0].rstrip("+")
                    if state == "COMPLETED" and row[2] == "0:0":
                        record = replace(record, state="succeeded", exit_code=0)
                        break
                    if state in {
                        "FAILED",
                        "CANCELLED",
                        "TIMEOUT",
                        "NODE_FAIL",
                        "OUT_OF_MEMORY",
                        "PREEMPTED",
                        "BOOT_FAIL",
                        "DEADLINE",
                        "COMPLETED",
                        "REVOKED",
                        "SPECIAL_EXIT",
                    }:
                        terminal_state: JobState = "failed"
                        if state == "TIMEOUT" or record.state == "timed_out":
                            terminal_state = "timed_out"
                        elif state == "CANCELLED" or record.state == "cancelled":
                            terminal_state = "cancelled"
                        detail = f"Slurm {state}: {row[2]}"
                        record = replace(
                            record,
                            state=terminal_state,
                            error=f"{record.error}; {detail}" if record.error else detail,
                        )
                        break
                    if state == "RUNNING" and record.started_at is None:
                        record = replace(
                            record,
                            state=record.state if record.terminal else "running",
                            started_at=time.time(),
                        )
                        write_record(path, record)
                if release_error is not None:
                    raise release_error
                if (
                    deadline is not None
                    and time.time() >= deadline
                    and record.state not in {"timed_out", "cancelled"}
                ):
                    await _scheduler_command("scancel", remote_id)
                    record = replace(
                        record,
                        state="timed_out",
                        error="Slurm observation timeout",
                        resources_released=False,
                    )
                    write_record(path, record)
                await asyncio.sleep(1)
            record = replace(
                record,
                updated_at=time.time(),
                finished_at=time.time(),
                resources_released=True,
                release_pending=False,
            )
            write_record(path, record)
        except asyncio.CancelledError:
            # Keep remote work reconnectable when a controller disconnects.
            write_record(path, record)
            raise
        if record.state != "succeeded":
            raise JobExecutionError(record)
        return record

    async def cancel(self, run_id: str, name: str) -> JobRecord:
        """Cancel only an execution whose exact identity belongs to this run."""
        record = self.detail(run_id, name)
        if record.terminal and record.resources_released:
            return record
        if record.remote_id is not None:
            await _scheduler_command("scancel", record.remote_id)
            # scancel acknowledges a request, not completed resource release.
            record = replace(
                record,
                state="cancelled",
                error="Host requested cancellation",
                resources_released=False,
            )
        elif record.pid is not None:
            identity = ProcessIdentity.read(record.pid)
            if identity is not None and identity.started != record.process_identity:
                raise RuntimeError("Refusing to cancel a reused process identity")
            released = identity is None or await asyncio.to_thread(
                terminate_process_tree, record.pid
            )
            record = replace(
                record, state="cancelled", resources_released=released, finished_at=time.time()
            )
        else:
            raise RuntimeError("Execution outcome is unobserved; reconcile before cancellation")
        write_record(self.directory(run_id, name) / "job.json", record)
        return record

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> bool:
        released = await asyncio.to_thread(terminate_process_tree, process.pid)
        with suppress(TimeoutError):
            await asyncio.wait_for(process.wait(), 5)
        return released and process.returncode is not None
