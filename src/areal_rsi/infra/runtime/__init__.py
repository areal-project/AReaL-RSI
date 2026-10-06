"""Run local tools with bounded lifetime for their entire process group."""

from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import BinaryIO


def run_process(
    command: Sequence[str],
    *,
    timeout: float | None,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    stdout: int | BinaryIO = subprocess.PIPE,
    stderr: int | BinaryIO = subprocess.PIPE,
) -> subprocess.CompletedProcess[bytes]:
    """Wait for a command; terminate its tools as well when execution fails."""
    with subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=stderr,
        start_new_session=True,
    ) as process:
        try:
            output, errors = process.communicate(timeout=timeout)
        except BaseException as error:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                output, errors = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                output, errors = process.communicate()
            finally:
                # A child can outlive an exited parent without inheriting pipes.
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            if isinstance(error, subprocess.TimeoutExpired):
                error.output, error.stderr = output, errors
            raise
        return subprocess.CompletedProcess(command, process.returncode, output, errors)
