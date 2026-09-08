"""Detached, standard-library-only job supervisor.

This file is executed directly so recovery does not depend on the submitter's
working directory or on a currently running Python service. It owns timeout and
cancellation and writes a durable result even when the service is restarted.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def process_identity(pid: int) -> str | None:
    """Birth time plus command prevents accidental signals after PID reuse."""
    if pid <= 0:
        return None
    try:
        if sys.platform.startswith("linux"):
            root = Path("/proc") / str(pid)
            # comm can contain spaces and parentheses; starttime is field 22.
            fields = (root / "stat").read_text().rsplit(")", 1)[1].split()
            if fields[0] == "Z":
                return None
            command = (root / "cmdline").read_bytes().decode(errors="replace")
            return fields[19] + ":" + command
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "stat=", "-o", "lstart=", "-o", "command="],
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
        value = result.stdout.strip()
        if result.returncode or not value or value.startswith("Z"):
            return None
        # Exclude the mutable status column.
        return value.split(maxsplit=1)[1]
    except (OSError, IndexError, subprocess.TimeoutExpired):
        return None


def group_exists(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    # macOS can report an orphaned, zombie-only group as existing but reject a
    # subsequent killpg with EPERM. Zombies hold no GPU and cannot execute.
    try:
        listing = subprocess.run(
            ["ps", "-axo", "pgid=,stat="],
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
        if listing.returncode:
            return True
        return any(
            len(fields := line.split()) == 2
            and fields[0] == str(pid)
            and not fields[1].startswith("Z")
            for line in listing.stdout.splitlines()
        )
    except (OSError, subprocess.TimeoutExpired):
        return True


def terminate_group(pid: int, *, grace_seconds: float = 0.5) -> None:
    """Caller must establish ownership before invoking this function."""
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace_seconds
    while group_exists(pid) and time.monotonic() < deadline:
        time.sleep(0.025)
    if not group_exists(pid):
        return
    with suppress(ProcessLookupError):
        os.killpg(pid, signal.SIGKILL)


def run(directory: Path, token: str) -> int:
    launch = read_json(directory / "launch.json")
    if launch is None or launch.get("token") != token:
        return 2
    # A stale supervisor must never start a job a second time.
    try:
        descriptor = os.open(
            directory / "started.lock", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
        )
    except FileExistsError:
        return 2
    os.close(descriptor)
    interrupted = False

    def stop(_signal: int, _frame: Any) -> None:
        nonlocal interrupted
        interrupted = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    handle: dict[str, Any] = {
        "token": token,
        "pid": os.getpid(),
        "process_identity": process_identity(os.getpid()),
        "started_at": time.time(),
        "child_pid": None,
        "child_identity": None,
    }
    write_json(directory / "handle.json", handle)
    spec = launch["spec"]
    started = time.monotonic()
    timeout = spec["timeout_seconds"]
    process: subprocess.Popen[bytes] | None = None
    state = "failed"
    exit_code: int | None = None
    error: str | None = None
    resources_released = True
    try:
        descriptor = os.open(
            directory / "stdout.log", os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600
        )
        with os.fdopen(descriptor, "wb", buffering=0) as log:
            if spec["backend"] == "mock":
                log.write(spec["mock_stdout"].encode())
            else:
                environment = {**os.environ, **spec["env"]}
                # Hide unallocated devices even when the host exposes GPUs.
                environment["CUDA_VISIBLE_DEVICES"] = ",".join(launch["assigned_gpu_ids"])
                process = subprocess.Popen(
                    spec["command"],
                    cwd=spec["cwd"],
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                handle.update(
                    child_pid=process.pid,
                    child_identity=process_identity(process.pid),
                )
                write_json(directory / "handle.json", handle)
            while True:
                cancellation = read_json(directory / "cancel.json")
                if interrupted or (cancellation is not None and cancellation.get("token") == token):
                    state = "cancelled"
                    break
                elapsed = time.monotonic() - started
                if timeout is not None and elapsed >= timeout:
                    state = "timed_out"
                    break
                if process is None:
                    if elapsed >= spec["mock_duration_seconds"]:
                        exit_code = spec["mock_exit_code"]
                        state = "succeeded" if exit_code == 0 else "failed"
                        break
                else:
                    exit_code = process.poll()
                    if exit_code is not None:
                        state = "succeeded" if exit_code == 0 else "failed"
                        break
                time.sleep(0.05)
    except BaseException as exception:
        error = f"{type(exception).__name__}: {exception}"
        state = "failed"
    finally:
        if process is not None:
            # Also reap descendants left behind after the root command exits.
            try:
                terminate_group(process.pid)
                exit_code = process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired) as exception:
                state = "lost"
                resources_released = False
                error = f"Unable to confirm child termination: {exception}"
        write_json(
            directory / "result.json",
            {
                "token": token,
                "backend": spec["backend"],
                "state": state,
                "exit_code": exit_code,
                "finished_at": time.time(),
                "error": error,
                "resources_released": resources_released,
            },
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(run(Path(sys.argv[1]), sys.argv[2]))
