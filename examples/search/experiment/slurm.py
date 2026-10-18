"""Submit held Slurm jobs, persist receipts, and recover release attempts."""

from __future__ import annotations

import fcntl
import os
import shlex
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from areal_rsi.infra.artifacts import read_record, write_record
from areal_rsi.infra.execution import JobExecutor
from examples.search.experiment.config import SearchConfig


@dataclass(frozen=True, slots=True)
class SlurmSubmission:
    job_id: str
    kind: str
    gpus: int
    release_pending: bool = False


def release_submission(receipt: Path, job: SlurmSubmission) -> str:
    if job.release_pending:
        try:
            status = subprocess.check_output(
                ["scontrol", "show", "job", "-o", job.job_id], text=True
            )
            fields = set(status.split())
            if (
                f"JobId={job.job_id}" not in fields
                or not any(field.startswith("JobState=") for field in fields)
                or not any(field.startswith("Reason=") for field in fields)
                or "Reason=JobHeldAdmin" in fields
            ):
                raise RuntimeError("Cannot confirm the submitted job's release state")
            if "Reason=JobHeldUser" in fields or (
                "JobState=PENDING" in fields and "Priority=0" in fields
            ):
                subprocess.check_call(["scontrol", "release", job.job_id])
            write_record(receipt, replace(job, release_pending=False))
        except Exception as exc:
            raise RuntimeError(
                f"Slurm job {job.job_id} is recorded at {receipt}; release is not confirmed. "
                "Inspect the job and retry the same run ID."
            ) from exc
    return job.job_id


def submit(
    config_path: Path,
    run_id: str,
    image: Path,
    time_limit: int,
    *,
    evaluation_split: Literal["dev", "test"] | None = None,
) -> str:
    """Submit a candidate or independent evaluation, retaining its Slurm receipt."""
    config = read_record(config_path, SearchConfig)
    gpus = 2 if evaluation_split else config.actor_gpus + config.rollout_gpus + 1
    kind = f"external-{evaluation_split}-evaluation" if evaluation_split else "training-candidate"
    time_limit = ((time_limit + 59) // 60) * 60
    if not 0 < time_limit <= 24 * 3600 or not config.project:
        raise ValueError("Submission requires a frozen project path and a positive time limit")
    project = Path(config.project).resolve(strict=True)
    entrypoint = project / "examples/search/experiment/pipeline.py"
    if not entrypoint.is_file():
        raise ValueError("Frozen project has no search pipeline entrypoint")
    root = Path(config.output_root).resolve()
    JobExecutor(root).directory(run_id, "train")  # Validate the caller-owned identifier.
    submissions = root / "submissions"
    submissions.mkdir(parents=True, exist_ok=True)
    directory = submissions / run_id
    with (submissions / f"{run_id}.lock").open("a") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        receipt = directory / "slurm-job.json"
        if receipt.exists():
            job = read_record(receipt, SlurmSubmission)
            if (
                job.kind != kind
                or job.gpus != gpus
                or read_record(directory / "config.json", SearchConfig) != config
            ):
                raise ValueError("Existing submission has a different configuration or mode")
            return release_submission(receipt, job)
        if directory.exists():
            raise RuntimeError(
                f"Incomplete submission at {directory}; inspect Slurm job name search-{run_id} "
                "before retrying. Automatic resubmission is disabled."
            )
        directory.mkdir(parents=True, exist_ok=False)
        frozen_config = directory / "config.json"
        write_record(frozen_config, config)
        overlay = config.runtime.environment.get("PYTHONPATH", "")
        search_path = os.pathsep.join(
            [str(project / "src"), str(project), config.runtime.repository, overlay]
        )
        command = [
            config.runtime.python,
            str(entrypoint),
            "--config",
            str(frozen_config),
            "--run-id",
            run_id,
        ]
        if evaluation_split:
            command.extend(("--evaluate-only", evaluation_split))
        # Pass paths as argv with shell quoting; neither config text nor credentials
        # become executable shell substitutions. The planner key is not exported.
        inside = "\n".join(
            [
                "set -euo pipefail",
                "unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy",
                "export NO_PROXY='*' no_proxy='*' HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1",
                "export PYTHONDONTWRITEBYTECODE=1",
                "export OMP_NUM_THREADS=8 MKL_NUM_THREADS=8 TOKENIZERS_PARALLELISM=false",
                "export PYTHONPATH=" + shlex.quote(search_path),
                "cd " + shlex.quote(str(project)),
                "cudnn_lib=$("
                + shlex.quote(config.runtime.python)
                + " -c "
                + shlex.quote(
                    "from importlib.metadata import distribution; "
                    'print(distribution("nvidia-cudnn-cu12").locate_file("nvidia/cudnn/lib"))'
                )
                + ")",
                'export LD_LIBRARY_PATH="$cudnn_lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"',
                "exec " + shlex.join(command),
            ]
        )
        (directory / "inside.sh").write_text(inside + "\n")
        script = directory / "run.sbatch"
        script.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            'allocation_info=$(scontrol show job -o "$SLURM_JOB_ID")\n'
            'export SEARCH_SLURM_JOB_INFO="$allocation_info"\n'
            + shlex.join(
                [
                    "srun",
                    "--mpi=none",
                    "apptainer",
                    "exec",
                    "--no-eval",
                    "--nv",
                    "--bind",
                    "/storage:/storage",
                    str(image.resolve(strict=True)),
                    "bash",
                    str(directory / "inside.sh"),
                ]
            )
            + "\n"
        )
        args = [
            "sbatch",
            "--parsable",
            "--hold",
            "--reservation=rsi",
            "--partition=all",
            "--nodes=1",
            "--ntasks=1",
            f"--gres=gpu:{gpus}",
            "--cpus-per-task=32",
            "--mem=256G",
            f"--time={time_limit // 60 + bool(time_limit % 60)}",
            f"--job-name=search-{run_id}",
            f"--output={directory}/slurm-%j.log",
            str(script),
        ]
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in {"RSI_API_KEY", "OPENAI_API_KEY", "SEARCH_PROXY_ADMIN_KEY"}
        }
        job_id = subprocess.check_output(args, env=environment, text=True).strip().split(";")[0]
        job = SlurmSubmission(job_id, kind, gpus, release_pending=True)
        try:
            write_record(receipt, job)
        except BaseException as exc:
            try:
                subprocess.check_call(["scancel", job_id])
            except Exception as cancel_error:
                raise RuntimeError(
                    f"Slurm job {job_id} was submitted held, but its receipt could not be saved "
                    "and cancellation was not confirmed. "
                    f"Inspect it with scontrol show job {job_id}."
                ) from cancel_error
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise RuntimeError(
                f"Slurm job {job_id} was cancelled because its receipt could not be saved."
            ) from exc
        return release_submission(receipt, job)
