"""Durable identity for an external AReaL recipe process."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class JobIntent:
    stage: str
    run_id: str
    command: tuple[str, ...]
    command_digest: str
    output_path: str


@dataclass(frozen=True, slots=True)
class JobReceipt:
    stage: str
    command_digest: str
    output_digest: str
    log_path: str
    elapsed_seconds: float
    slurm_job_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ResearchIntent:
    run_id: str
    h0_id: str
    feedback_digest: str
    researcher_model: str
    researcher_endpoint: str
