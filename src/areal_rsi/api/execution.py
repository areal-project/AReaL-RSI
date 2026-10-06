"""Durable identity for an external AReaL recipe process."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from areal_rsi.api.control import safe_name
from areal_rsi.api.records import valid_digest


@dataclass(frozen=True, slots=True)
class AgentExecutionSpec:
    """Execution limits shared by task-specific Claude Code harnesses."""

    model: str
    timeout_seconds: int | None = None
    max_budget_usd: float | None = None

    def __post_init__(self) -> None:
        if (
            not self.model.strip()
            or (
                self.timeout_seconds is not None
                and (type(self.timeout_seconds) is not int or self.timeout_seconds < 1)
            )
            or (
                self.max_budget_usd is not None
                and (not math.isfinite(self.max_budget_usd) or self.max_budget_usd <= 0)
            )
        ):
            raise ValueError("Invalid agent execution limits")


@dataclass(frozen=True, slots=True)
class McpTool:
    """One explicitly allowed tool exposed by a local stdio MCP server."""

    server: str
    name: str
    command: str
    arguments: tuple[str, ...]

    def __post_init__(self) -> None:
        from pathlib import Path

        safe_name(self.server)
        safe_name(self.name)
        if not Path(self.command).is_absolute():
            raise ValueError("MCP command must be absolute")


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    response: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    duration_ms: int


@dataclass(frozen=True, slots=True)
class JobIntent:
    stage: str
    run_id: str
    command: tuple[str, ...]
    command_digest: str
    output_path: str

    def __post_init__(self) -> None:
        from pathlib import Path

        safe_name(self.stage)
        safe_name(self.run_id)
        if (
            not self.command
            or not Path(self.command[0]).is_absolute()
            or not Path(self.output_path).is_absolute()
            or not valid_digest(self.command_digest)
        ):
            raise ValueError("AReaL job intent has invalid command or output identity")


@dataclass(frozen=True, slots=True)
class JobReceipt:
    stage: str
    command_digest: str
    output_digest: str
    log_path: str
    elapsed_seconds: float | None
    slurm_job_ids: tuple[int, ...]
    completion_source: Literal["process", "recovered"]

    def __post_init__(self) -> None:
        from pathlib import Path

        safe_name(self.stage)
        if (
            (self.completion_source == "process" and self.elapsed_seconds is None)
            or (self.completion_source == "recovered" and self.elapsed_seconds is not None)
            or (
                not valid_digest(self.command_digest)
                or not valid_digest(self.output_digest)
                or not Path(self.log_path).is_absolute()
                or (self.elapsed_seconds is not None and self.elapsed_seconds < 0)
                or any(type(value) is not int or value < 1 for value in self.slurm_job_ids)
            )
        ):
            raise ValueError("AReaL job receipt has invalid provenance or duration")


@dataclass(frozen=True, slots=True)
class ResearchIntent:
    run_id: str
    h0_id: str
    feedback_digest: str
    researcher_model: str
    researcher_endpoint: str


@dataclass(frozen=True, slots=True)
class SlurmPreflight:
    sbatch_path: str
    squeue_path: str
    scancel_path: str
    srun_path: str
    scontrol_path: str
    sbatch_version: str
    squeue_version: str
    scancel_version: str
    srun_version: str
    scontrol_version: str
    cluster_fileroot: str
    name_resolve_root: str

    def __post_init__(self) -> None:
        from pathlib import Path

        if any(
            not Path(value).is_absolute()
            for value in (
                self.sbatch_path,
                self.squeue_path,
                self.scancel_path,
                self.srun_path,
                self.scontrol_path,
                self.cluster_fileroot,
                self.name_resolve_root,
            )
        ) or any(
            not value.strip()
            for value in (
                self.sbatch_version,
                self.squeue_version,
                self.scancel_version,
                self.srun_version,
                self.scontrol_version,
            )
        ):
            raise ValueError("Slurm preflight lacks command or shared-path evidence")


@dataclass(frozen=True, slots=True)
class TrustedSourceFile:
    label: str
    path: Path
