"""Crash-aware subprocess boundary for AReaL recipe scripts."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import time
from pathlib import Path

from areal_rsi.api.control import json_bytes, read_record, safe_name
from areal_rsi.api.execution import JobIntent, JobReceipt
from areal_rsi.infra.storage.files import write_bytes


class UncertainAReaLJob(RuntimeError):
    """A previous submission needs operator reconciliation before retry."""


def digest_bytes(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


class AReaLJobRunner:
    def __init__(
        self,
        root: Path,
        run_id: str,
        *,
        exclude_env: tuple[str, ...] = (),
        require_slurm_jobs: bool = False,
    ) -> None:
        self.root = root
        self.run_id = safe_name(run_id)
        self.exclude_env = exclude_env
        self.require_slurm_jobs = require_slurm_jobs

    def run(self, stage: str, command: tuple[str, ...], output: Path) -> Path:
        stage = safe_name(stage)
        if not output.is_absolute() or not command or not Path(command[0]).is_absolute():
            raise ValueError("AReaL recipe requires absolute command and output paths")
        self.root.mkdir(parents=True, exist_ok=True)
        command_digest = digest_bytes(json_bytes(command))
        intent = JobIntent(stage, self.run_id, command, command_digest, str(output))
        intent_path = self.root / f"{stage}.intent.json"
        receipt_path = self.root / f"{stage}.receipt.json"
        log_path = self.root / f"{stage}.log"
        if intent_path.exists():
            if read_record(intent_path, JobIntent) != intent:
                raise ValueError(f"Recipe command changed for stage {stage}")
            if not receipt_path.exists():
                raise UncertainAReaLJob(f"Reconcile AReaL stage {stage} before resubmitting")
            receipt = read_record(receipt_path, JobReceipt)
            if (
                receipt.command_digest != command_digest
                or receipt.stage != stage
                or not output.is_file()
                or digest_bytes(output.read_bytes()) != receipt.output_digest
            ):
                raise ValueError(f"AReaL stage {stage} output changed after receipt")
            if self.require_slurm_jobs and not receipt.slurm_job_ids:
                raise ValueError(f"AReaL stage {stage} has no Slurm job IDs")
            return output
        if output.exists() or receipt_path.exists():
            raise ValueError(f"Unregistered AReaL stage output exists: {stage}")
        write_bytes(intent_path, json_bytes(intent), immutable=True)
        started = time.monotonic()
        environment = os.environ.copy()
        for name in self.exclude_env:
            environment.pop(name, None)
        with log_path.open("xb") as log:
            result = subprocess.run(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
                env=environment,
            )
        if result.returncode != 0 or not output.is_file():
            raise UncertainAReaLJob(
                f"AReaL stage {stage} failed or produced no output; inspect {log_path}"
            )
        job_ids = tuple(
            sorted(
                {
                    int(value)
                    for value in re.findall(
                        r"Submitted batch job ([0-9]+)", log_path.read_text(errors="replace")
                    )
                }
            )
        )
        if self.require_slurm_jobs and not job_ids:
            raise UncertainAReaLJob(f"AReaL stage {stage} has no recorded Slurm job ID")
        receipt = JobReceipt(
            stage,
            command_digest,
            digest_bytes(output.read_bytes()),
            str(log_path),
            time.monotonic() - started,
            job_ids,
        )
        write_bytes(receipt_path, json_bytes(receipt), immutable=True)
        return output
