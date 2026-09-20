"""Run-local persistent scheduling with detached local process supervisors."""

from __future__ import annotations

import fcntl
import hashlib
import math
import os
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, cast

from areal_rsi.api.execution import TERMINAL_STATES, JobEvent, JobRecord, JobSpec, JobState

from ._runner import group_exists, process_identity, read_json, terminate_group, write_json


def _decode_record(value: dict[str, Any]) -> JobRecord:
    spec = {**value["spec"]}
    spec["command"] = tuple(spec["command"])
    spec["gpu_ids"] = tuple(spec["gpu_ids"])
    return JobRecord(
        **{
            **value,
            "spec": JobSpec(**spec),
            "assigned_gpu_ids": tuple(value["assigned_gpu_ids"]),
        }
    )


class JobManager:
    """Own a single run's queue, not the lifetime of its running commands.

    ``submit`` starts a detached supervisor when resources permit. ``poll``
    reconciles durable results and schedules queued jobs. ``start`` optionally
    does this on a lightweight thread; no model calls occur while jobs wait.

    A process whose identity cannot be established becomes ``lost``, never
    automatically retried. If termination cannot be proved, its resource claim
    is retained. PID numbers alone are insufficient evidence to send a signal.

    This is a local POSIX runner, not a sandbox. Trust only independent verifier
    output retrieved through the application's verifier adapter; neither logs
    nor a successful process exit constitutes an evaluation score.
    """

    def __init__(
        self,
        run_root: str | Path,
        gpu_ids: tuple[str, ...] | list[str] = (),
        max_jobs: int | None = 1,
        *,
        on_event: Callable[[JobEvent], None] | None = None,
        auto_monitor: bool = True,
        scheduling_enabled: bool = True,
        poll_interval: float = 0.1,
    ) -> None:
        if max_jobs is not None and max_jobs < 1:
            raise ValueError("max_jobs must be at least one")
        if not math.isfinite(poll_interval) or poll_interval <= 0:
            raise ValueError("poll_interval must be positive and finite")
        if len(set(gpu_ids)) != len(gpu_ids):
            raise ValueError("GPU IDs must be unique")
        resolved_run = Path(run_root).resolve()
        self.root = resolved_run / "jobs"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Run records remain canonical. The shared file contains no allocation
        # state; its flock only serializes discovering and persisting claims.
        self._shared_runs = resolved_run.parent if resolved_run.parent.name == "runs" else None
        self._gpu_lock_path = (
            self._shared_runs.parent / "runtime/gpu-allocation.lock"
            if self._shared_runs is not None
            else self.root / "gpu-allocation.lock"
        )
        self.gpu_ids = tuple(gpu_ids)
        self.max_jobs = max_jobs
        self.scheduling_enabled = scheduling_enabled
        self.on_event = on_event
        self.callback_error: str | None = None
        self.poll_interval = poll_interval
        self._mutex = threading.RLock()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._closed = False
        descriptor = os.open(self.root / "manager.lock", os.O_CREAT | os.O_RDWR, 0o600)
        self._lock_file = os.fdopen(descriptor, "r+")
        try:
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exception:
            self._lock_file.close()
            raise RuntimeError(f"Another job manager owns {self.root}") from exception
        try:
            self.poll()
            if auto_monitor:
                self.start()
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> JobManager:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def start(self) -> None:
        with self._mutex:
            self._ensure_open()
            if self._thread is not None:
                return
            self._thread = threading.Thread(target=self._monitor, name="rsi-jobs", daemon=True)
            self._thread.start()

    def _monitor(self) -> None:
        while not self._stopping.wait(self.poll_interval):
            try:
                self.poll()
            except Exception as exception:
                # Keep the supervisor independent of monitoring/notification failures.
                self.callback_error = f"{type(exception).__name__}: {exception}"

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Job manager is closed")

    def _directory(self, job_id: str) -> Path:
        if not job_id.startswith("job-") or not job_id[4:].isalnum():
            raise ValueError("Invalid job ID")
        return self.root / job_id

    def get(self, job_id: str) -> JobRecord:
        value = read_json(self._directory(job_id) / "job.json")
        if value is None:
            raise KeyError(job_id)
        return _decode_record(value)

    def records(self) -> tuple[JobRecord, ...]:
        with self._mutex:
            records = []
            for path in self.root.glob("job-*/job.json"):
                value = read_json(path)
                if value is not None:
                    records.append(_decode_record(value))
            return tuple(sorted(records, key=lambda record: (record.created_at, record.job_id)))

    def submit(
        self,
        spec: JobSpec,
        *,
        job_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> JobRecord:
        with self._mutex:
            self._ensure_open()
            spec = self._validate_spec(spec)
            if job_id is not None and idempotency_key is not None:
                raise ValueError("Specify job_id or idempotency_key, not both")
            if idempotency_key is not None:
                job_id = "job-" + hashlib.sha256(idempotency_key.encode()).hexdigest()
            job_id = job_id or "job-" + uuid.uuid4().hex
            directory = self._directory(job_id)
            if (directory / "job.json").exists():
                existing = self.get(job_id)
                if existing.spec != spec:
                    raise ValueError("Job idempotency key already belongs to a different spec")
                self._schedule()
                self._dispatch_events()
                return self.get(job_id)
            directory.mkdir(mode=0o700, exist_ok=True)
            now = time.time()
            record = JobRecord(
                job_id=job_id,
                spec=spec,
                state="queued",
                created_at=now,
                updated_at=now,
                log_path=str(directory / "stdout.log"),
                result_path=str(directory / "result.json"),
            )
            self._store(record)
            self._schedule()
            self._dispatch_events()
            return self.get(job_id)

    def _validate_spec(self, spec: JobSpec) -> JobSpec:
        if spec.backend not in {"local", "mock"}:
            raise ValueError("Unsupported job backend")
        if spec.backend == "local" and (not spec.command or not spec.command[0]):
            raise ValueError("Local jobs require a command argv")
        if spec.gpu_count < 0 or spec.gpu_count > len(self.gpu_ids):
            raise ValueError("Requested GPU count exceeds this run's allocation")
        if len(set(spec.gpu_ids)) != len(spec.gpu_ids) or not set(spec.gpu_ids) <= set(
            self.gpu_ids
        ):
            raise ValueError("Requested GPU IDs are outside this run's allocation or duplicated")
        if spec.gpu_ids and spec.gpu_count not in {0, len(spec.gpu_ids)}:
            raise ValueError("gpu_count and explicit gpu_ids disagree")
        if spec.timeout_seconds is not None and (
            not math.isfinite(spec.timeout_seconds) or spec.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be positive and finite")
        if not math.isfinite(spec.mock_duration_seconds) or spec.mock_duration_seconds < 0:
            raise ValueError("mock_duration_seconds must be nonnegative and finite")
        cwd = Path(spec.cwd).resolve()
        if spec.backend == "local" and not cwd.is_dir():
            raise ValueError("Job working directory does not exist")
        return replace(spec, cwd=str(cwd), command=tuple(spec.command), gpu_ids=tuple(spec.gpu_ids))

    def _event(self, record: JobRecord) -> JobEvent:
        return JobEvent(
            event_id=f"{record.job_id}-{record.revision}",
            job_id=record.job_id,
            state=record.state,
            timestamp=record.updated_at,
            record_path=str(self._directory(record.job_id) / "job.json"),
        )

    def _store(self, record: JobRecord) -> None:
        write_json(self._directory(record.job_id) / "job.json", asdict(record))
        self._ensure_event(record)

    def _ensure_event(self, record: JobRecord) -> None:
        event = self._event(record)
        path = self.root / "events" / f"{event.event_id}.json"
        if not path.exists():
            write_json(path, {"event": asdict(event), "delivered": False})

    def _transition(self, record: JobRecord, **changes: Any) -> JobRecord:
        updated = replace(record, revision=record.revision + 1, updated_at=time.time(), **changes)
        self._store(updated)
        return updated

    def _dispatch_events(self) -> None:
        if self.on_event is None:
            return
        pending: list[tuple[Path, dict[str, Any]]] = []
        for path in (self.root / "events").glob("*.json"):
            value = read_json(path)
            if value is not None and not value["delivered"]:
                pending.append((path, value))
        pending.sort(key=lambda item: (item[1]["event"]["timestamp"], item[0].name))
        for path, value in pending:
            try:
                self.on_event(JobEvent(**value["event"]))
            except Exception as exception:
                self.callback_error = f"{type(exception).__name__}: {exception}"
                # Preserve event ordering; a later poll retries this notification.
                break
            write_json(path, {**value, "delivered": True})

    def _schedule(self) -> None:
        if not self.scheduling_enabled:
            return
        records = self.records()
        if not any(record.state == "queued" for record in records):
            return
        if not self.gpu_ids:
            self._schedule_records(records, set())
            return
        self._gpu_lock_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self._gpu_lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(descriptor, "r+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                # Another run is publishing a claim. Leave this job queued;
                # the monitor/manual poll can try again without a busy wait.
                return
            try:
                self._schedule_records(records, self._other_run_gpu_claims())
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _other_run_gpu_claims(self) -> set[str]:
        """Read-only reconciliation of claims belonging to other run owners.

        An offline manager does not make its allocation available. Only a
        persisted resource release or an authenticated supervisor completion can
        release it. In particular, a missing PID is not evidence of free GPUs:
        orphan child processes may still exist.
        """
        if self._shared_runs is None:
            return set()
        occupied: set[str] = set()
        for path in self._shared_runs.glob("*/jobs/job-*/job.json"):
            if path.parent.parent == self.root:
                continue
            record = read_json(path)
            if record is None or record.get("resources_released") is True:
                continue
            gpu_ids = record.get("assigned_gpu_ids", [])
            if not isinstance(gpu_ids, list) or any(not isinstance(gpu, str) for gpu in gpu_ids):
                raise ValueError(f"Invalid GPU resource declaration: {path}")
            if not gpu_ids:
                continue
            result = read_json(path.parent / "result.json")
            token = record.get("launch_token")
            if (
                isinstance(token, str)
                and token
                and result is not None
                and result.get("token") == token
                and result.get("state") in TERMINAL_STATES
                and result.get("resources_released", result.get("state") != "lost") is True
            ):
                # The owning manager can update its own job/events later. Do
                # not mutate another run's records merely to reuse a free GPU.
                continue
            occupied.update(gpu_ids)
        return occupied

    def _schedule_records(self, records: tuple[JobRecord, ...], foreign_gpus: set[str]) -> None:
        claimed = [record for record in records if not record.resources_released]
        used = foreign_gpus | {gpu for record in claimed for gpu in record.assigned_gpu_ids}
        available_slots = (
            sum(record.state == "queued" for record in records)
            if self.max_jobs is None
            else self.max_jobs - len(claimed)
        )
        for record in records:
            if record.state != "queued" or available_slots <= 0:
                continue
            requested = record.spec.gpu_ids
            if requested:
                if not set(requested) <= set(self.gpu_ids) or set(requested) & used:
                    continue
                allocated = requested
            else:
                available = [gpu for gpu in self.gpu_ids if gpu not in used]
                if len(available) < record.spec.gpu_count:
                    continue
                allocated = tuple(available[: record.spec.gpu_count])
            launched = self._launch(record, allocated)
            if not launched.resources_released:
                used.update(allocated)
                available_slots -= 1

    def _launch(self, record: JobRecord, gpu_ids: tuple[str, ...]) -> JobRecord:
        directory = self._directory(record.job_id)
        token = uuid.uuid4().hex
        record = self._transition(
            record,
            state="starting",
            started_at=time.time(),
            assigned_gpu_ids=gpu_ids,
            launch_token=token,
            resources_released=False,
        )
        write_json(
            directory / "launch.json",
            {"token": token, "spec": asdict(record.spec), "assigned_gpu_ids": list(gpu_ids)},
        )
        try:
            descriptor = os.open(
                directory / "supervisor.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
            )
            with os.fdopen(descriptor, "ab", buffering=0) as log:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        str(Path(__file__).with_name("_runner.py")),
                        str(directory),
                        token,
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            self._processes[record.job_id] = process
            return self._transition(
                record,
                state="running",
                pid=process.pid,
                process_identity=process_identity(process.pid),
            )
        except OSError as exception:
            return self._transition(
                record,
                state="failed",
                error=f"Supervisor launch failed: {exception}",
                finished_at=time.time(),
                resources_released=True,
            )

    def poll(self) -> tuple[JobRecord, ...]:
        with self._mutex:
            self._ensure_open()
            for job_id, process in tuple(self._processes.items()):
                if process.poll() is not None:
                    del self._processes[job_id]
            for record in self.records():
                self._ensure_event(record)
                if record.state in {"starting", "running"} or (
                    record.state == "lost" and not record.resources_released
                ):
                    self._reconcile(record)
            self._schedule()
            self._dispatch_events()
            return self.records()

    def _reconcile(self, record: JobRecord) -> None:
        directory = self._directory(record.job_id)
        result = read_json(directory / "result.json")
        if result is not None and result.get("token") == record.launch_token:
            state = result.get("state")
            if state not in TERMINAL_STATES:
                raise ValueError(f"Invalid supervisor result for {record.job_id}")
            if record.state == state and record.finished_at == result["finished_at"]:
                if record.state == "lost":
                    self._resolve_lost_claim(record)
                return
            self._transition(
                record,
                state=cast(JobState, state),
                exit_code=result.get("exit_code"),
                error=result.get("error"),
                finished_at=result["finished_at"],
                resources_released=result.get("resources_released", state != "lost"),
            )
            return
        handle = read_json(directory / "handle.json")
        if handle is not None and handle.get("token") != record.launch_token:
            handle = None
        pid = handle["pid"] if handle is not None else record.pid
        identity = handle["process_identity"] if handle is not None else record.process_identity
        actual = process_identity(pid) if pid is not None else None
        if identity is not None and identity == actual:
            if record.state in {"starting", "lost"}:
                self._transition(
                    record, state="running", pid=pid, process_identity=identity, error=None
                )
            return
        # Give the exec/handle write a bounded grace period after a launch.
        if time.time() - (record.started_at or record.updated_at) < 3:
            return
        # Re-read after checking a process that may have just exited normally.
        if (directory / "result.json").exists():
            result = read_json(directory / "result.json")
            if result is not None and result.get("token") == record.launch_token:
                self._reconcile(record)
                return
        if record.state == "lost":
            self._resolve_lost_claim(record)
            return
        released = self._clean_orphan(handle)
        self._transition(
            record,
            state="lost",
            finished_at=time.time(),
            resources_released=released,
            error=(
                "Supervisor disappeared without a durable result; this job will not be retried. "
                + (
                    "Owned child processes have stopped."
                    if released
                    else "Process ownership is uncertain; resource allocation remains reserved."
                )
            ),
        )

    def _resolve_lost_claim(self, record: JobRecord) -> None:
        handle = read_json(self._directory(record.job_id) / "handle.json")
        if handle is None or handle.get("token") != record.launch_token:
            return
        pid, identity = handle.get("pid"), handle.get("process_identity")
        if isinstance(pid, int) and identity is not None and process_identity(pid) == identity:
            return
        if self._clean_orphan(handle):
            self._transition(
                record,
                resources_released=True,
                error=(
                    "Lost supervisor; owned child processes are now confirmed stopped. "
                    "Job was not retried."
                ),
            )

    def _clean_orphan(self, handle: dict[str, Any] | None) -> bool:
        if handle is None:
            return False
        child = handle.get("child_pid")
        if child is None:
            # A supervisor might have crashed between spawn and handle update.
            return False
        identity = handle.get("child_identity")
        actual = process_identity(child)
        if identity is not None and identity == actual:
            terminate_group(child)
            return process_identity(child) is None and not group_exists(child)
        # Never signal a reused PID or an unidentifiable group.
        return actual is None and not group_exists(child)

    def cancel(self, job_id: str) -> JobRecord:
        with self._mutex:
            self._ensure_open()
            record = self.get(job_id)
            if record.terminal and record.state != "lost":
                return record
            if record.state == "queued":
                record = self._transition(record, state="cancelled", finished_at=time.time())
                self._dispatch_events()
                return record
            write_json(
                self._directory(job_id) / "cancel.json",
                {"token": record.launch_token, "requested_at": time.time()},
            )
            # Supervisor receives cancellation through disk and remains alive to
            # reap the process group and write its result. No unverified kill(pid).
            return record

    def close(self, *, preserve_running: bool = True) -> None:
        if self._closed:
            return
        self._stopping.set()
        if self._thread is not None and threading.current_thread() is not self._thread:
            self._thread.join(timeout=5)
        with self._mutex:
            if not preserve_running:
                for record in self.records():
                    if not record.terminal or not record.resources_released:
                        self.cancel(record.job_id)
                deadline = time.monotonic() + 5
                while any(not record.terminal for record in self.records()):
                    self.poll()
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.05)
            for process in self._processes.values():
                process.poll()
            fcntl.flock(self._lock_file, fcntl.LOCK_UN)
            self._lock_file.close()
            self._closed = True
