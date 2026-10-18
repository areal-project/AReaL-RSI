"""Durable local subprocess receipts shared by evaluation and training jobs.

The caller owns resource allocation (e.g. an existing Slurm allocation); this is
not a second GPU scheduler. AReaL owns its workers inside that allocation.
"""

from __future__ import annotations

import asyncio
import math
import os
import re
import signal
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path

from areal_rsi.api.execution import JobRecord, JobSpec
from areal_rsi.infra.artifacts import read_record, write_record


class JobExecutionError(RuntimeError):
    def __init__(self, record: JobRecord):
        self.record = record
        super().__init__(
            f"Job {record.job_id}: {record.state}; {record.error}; log: {record.log_path}"
        )


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

    async def run(self, run_id: str, name: str, spec: JobSpec) -> JobRecord:
        if spec.backend != "local" or not spec.command:
            raise ValueError("A real executable command is required")
        if spec.timeout_seconds is not None and (
            not math.isfinite(spec.timeout_seconds) or spec.timeout_seconds <= 0
        ):
            raise ValueError("Job timeout must be positive")
        directory = self.directory(run_id, name)
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
                            f"{record.error or 'Execution ended'}; "
                            "process tree cleanup incomplete"
                        ),
                    )
            record = replace(
                record, updated_at=time.time(), finished_at=time.time(), resources_released=released
            )
            save()
        if record.state != "succeeded":
            raise JobExecutionError(record)
        return record

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> bool:
        released = await asyncio.to_thread(terminate_process_tree, process.pid)
        with suppress(TimeoutError):
            await asyncio.wait_for(process.wait(), 5)
        return released and process.returncode is not None
