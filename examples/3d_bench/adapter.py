"""3D dataset, isolated verifier and scoring shared by CLI and TUI."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

if __package__:
    from .environment import ModelEnvironment
else:
    from environment import ModelEnvironment

from areal_rsi.api.base import (
    BenchmarkSplit,
    EvaluationReport,
    SolutionVersion,
    Task,
    VerificationRequest,
    VerificationResult,
    VerificationStatus,
)
from areal_rsi.base.storage import (
    FileContentStore,
    decode,
    encode,
    new_id,
    now,
    safe_name,
    write_json,
)

SCOPE = "shared train/validate diagnostic; Contract V2 and build, no visual or held-out score"


def load_tasks(tasks_path: Path, *, task_ids: Sequence[str] | None = None) -> tuple[Task, ...]:
    """Read public targets without requiring a fixed-size private dataset."""
    tasks = {}
    for path in sorted(tasks_path.glob("*/TARGET.md")):
        match = re.fullmatch(r"(?:model-(\d{3})|(\d{3})-.+)", path.parent.name, re.IGNORECASE)
        if match is None:
            raise ValueError(f"Unexpected task directory: {path.parent.name}")
        task_id = f"MODEL-{int(match[1] or match[2]):03d}"
        if task_id in tasks:
            raise ValueError(f"Duplicate task ID: {task_id}")
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            raise ValueError(f"Empty target: {path}")
        tasks[task_id] = Task(
            task_id,
            text,
            {
                "target_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "source_directory": path.parent.name,
            },
        )
    if not tasks:
        raise ValueError(f"No TARGET.md tasks found under {tasks_path}")
    selected = set(tasks) if task_ids is None else set(task_ids)
    if not selected or (task_ids is not None and len(selected) != len(task_ids)):
        raise ValueError("Selected task IDs must be nonempty and unique")
    if not selected <= tasks.keys():
        raise ValueError("Unknown selected task IDs")
    return tuple(tasks[key] for key in sorted(selected))


class ThreeModelBenchmark:
    """An explicit same-suite diagnostic, with both required workflow splits."""

    def __init__(
        self, tasks: Path | Task | Sequence[Task], snapshot: Any = None, *, scope: str | None = None
    ) -> None:
        values = (
            load_tasks(tasks)
            if isinstance(tasks, Path)
            else ((tasks,) if isinstance(tasks, Task) else tuple(tasks))
        )
        if not values or len({task.task_id for task in values}) != len(values):
            raise ValueError("Tasks must be nonempty and unique")
        self.tasks = tuple(decode(Task, encode(task)) for task in values)
        label = self.tasks[0].task_id if len(self.tasks) == 1 else f"{len(self.tasks)} tasks"
        self.scope = scope if scope is not None else f"{label} {SCOPE}"
        identity = json.dumps(
            {"tasks": encode(self.tasks), "snapshot": snapshot, "scope": self.scope}, sort_keys=True
        ).encode()
        prefix = (
            f"3d-{label.lower().replace('-', '')}-diagnostic-"
            if len(values) == 1
            else ("3d-suite-diagnostic-")
        )
        self.name = prefix + hashlib.sha256(identity).hexdigest()[:16]

    async def list_tasks(self, split: BenchmarkSplit) -> Sequence[Task]:
        if split not in (BenchmarkSplit.TRAIN, BenchmarkSplit.VALIDATE):
            raise ValueError("Unsupported split")
        return tuple(decode(Task, encode(task)) for task in self.tasks)


class ThreeModelVerifier:
    """Always use host-owned validation in a fresh, isolated environment."""

    def __init__(
        self,
        root: Path,
        content: FileContentStore,
        emit: Callable[[str], None] = lambda _: None,
        *,
        environment: ModelEnvironment,
    ) -> None:
        self.root, self.content, self.emit = root.resolve(), content, emit
        self.environment = environment

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        result = request.result
        if result.output.media_type != "text/javascript":
            raise ValueError("Expected model.js file contents")
        source = await self.content.read_bytes(result.output)
        key = hashlib.sha256(json.dumps(request.metadata, sort_keys=True).encode()).hexdigest()
        folder = self.root / safe_name(result.trajectory.trajectory_id) / key[:16]
        folder.mkdir(parents=True, exist_ok=True)
        # Missing checkers, dependencies or sandbox support raise to VerifierExecutor,
        # which records INFRA_ERROR rather than a model score.
        checked = await asyncio.to_thread(
            self.environment.validate, source, folder / new_id("check")
        )
        passed = checked["passed"]
        feedback = "\n".join(
            f"{name}: {'passed' if item['returncode'] == 0 else 'failed'}\n{item['stderr']}"
            for name, item in checked["checks"].items()
        )
        if "receipt_error" in checked:
            feedback += "\n" + checked["receipt_error"]
        verification = VerificationResult(
            request.task.task_id,
            result.trajectory.trajectory_id,
            passed,
            float(passed),
            {
                "contract_v2": float(
                    checked["checks"]["contract"]["returncode"] == 0
                    and "receipt_error" not in checked
                ),
                "build": float(checked["checks"]["build"]["returncode"] == 0),
            },
            feedback,
            attachments=(await self.content.put_json(checked),),
        )
        write_json(folder / "verification.json", verification)
        self.emit(f"Verifier: passed={passed}; trajectory={result.trajectory.trajectory_id}")
        return verification


class ThreeModelEvaluation:
    async def evaluate(
        self,
        solution: SolutionVersion,
        split: BenchmarkSplit,
        results: Sequence[VerificationResult],
        *,
        baseline: EvaluationReport | None = None,
    ) -> EvaluationReport:
        if not results or any(item.status is not VerificationStatus.SUCCEEDED for item in results):
            raise ValueError("评测必须包含完整的验证结果；基础设施错误不能作为模型失败计分")
        if len({item.task_id for item in results}) != len(results):
            raise ValueError("评测不能重复包含同一个任务")
        score = sum(float(item.score or 0.0) for item in results) / len(results)
        return EvaluationReport(
            solution.solution_id,
            split,
            score == 1.0,
            score,
            {
                "contract_v2": sum(float(item.metrics.get("contract_v2", 0)) for item in results)
                / len(results),
                "task_count": float(len(results)),
                "pass_count": float(sum(item.passed is True for item in results)),
            },
            now(),
        )
