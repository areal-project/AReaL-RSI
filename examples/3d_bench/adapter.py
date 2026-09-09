"""Minimal Contract V2 benchmark implementation for one 3D task."""

from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

from areal_rsi.api.base import (
    BenchmarkSplit,
    EvaluationReport,
    SolutionVersion,
    Task,
    VerificationRequest,
    VerificationResult,
    VerificationStatus,
)
from areal_rsi.base.storage import FileContentStore, now
from areal_rsi.benchmarks.catalog import BenchmarkBundle


class ThreeModelBenchmark:
    name = "3d-model-v1"

    def __init__(self, tasks_path: Path, task_id: str = "MODEL-001") -> None:
        self.tasks_path = tasks_path
        self.task_id = task_id
        # Accept the short public example name and legacy task directories.
        matches = list(tasks_path.glob(f"{task_id[6:]}-*"))
        public_directory = tasks_path / task_id.lower()
        if public_directory.is_dir():
            matches.append(public_directory)
        if len(matches) != 1:
            raise ValueError(f"无法唯一定位 {task_id} TARGET.md: {tasks_path}")
        self.target_path = matches[0] / "TARGET.md"
        if not self.target_path.is_file():
            raise ValueError(f"缺少 TARGET.md: {self.target_path}")

    async def list_tasks(self, split: BenchmarkSplit) -> Sequence[Task]:
        if split is not BenchmarkSplit.TRAIN:
            return ()
        return (Task(self.task_id, self.target_path.read_text(encoding="utf-8")),)


def load_tasks(tasks_path: Path) -> tuple[Task, ...]:
    """Read all 100 public targets in numeric order; reject gaps and ambiguous IDs."""
    tasks = {}
    for path in tasks_path.glob("*/TARGET.md"):
        match = re.fullmatch(r"(\d{3})-.+", path.parent.name)
        if match is None:
            raise ValueError(f"Unexpected task directory: {path.parent.name}")
        number = int(match[1])
        if number in tasks or not 1 <= number <= 100:
            raise ValueError(f"Duplicate or out-of-range task number: {number}")
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            raise ValueError(f"Empty target: {path}")
        tasks[number] = Task(
            f"MODEL-{number:03d}",
            text,
            {
                "target_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "source_directory": path.parent.name,
            },
        )
    if set(tasks) != set(range(1, 101)):
        raise ValueError(f"Expected exactly MODEL-001..MODEL-100, found {len(tasks)} tasks")
    return tuple(tasks[number] for number in sorted(tasks))


class ThreeModelVerifier:
    def __init__(self, content: FileContentStore, runtime_path: Path) -> None:
        self.content = content
        self.runtime_path = runtime_path

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        result = request.result
        if result.output.media_type not in (None, "text/javascript", "text/plain; charset=utf-8"):
            raise ValueError("3D verifier 要求 model.js 文件型 TaskResult.output")
        source = await self.content.resolve_path(result.output)
        if not source.is_file():
            raise ValueError("3D TaskResult.output 必须是 model.js 文件内容")
        with tempfile.TemporaryDirectory(prefix="rsi-3d-verify-") as temporary:
            workspace = Path(temporary) / "viewer"
            template = self.runtime_path / "viewer-template"
            if not template.is_dir():
                template = self.runtime_path
            shutil.copytree(template, workspace)
            shutil.copy2(source, workspace / "src/model.js")
            completed = await asyncio.to_thread(
                subprocess.run,
                ["node", "scripts/check-model.mjs"],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=120,
            )
        passed = completed.returncode == 0
        feedback = (completed.stdout + completed.stderr).strip()[-4000:]
        payload = {"passed": passed, "returncode": completed.returncode, "output": feedback}
        output_ref = await self.content.put_json(payload)
        return VerificationResult(
            request.task.task_id,
            result.trajectory.trajectory_id,
            passed,
            1.0 if passed else 0.0,
            {"contract_v2": 1.0 if passed else 0.0},
            feedback,
            attachments=(output_ref,),
            status=VerificationStatus.SUCCEEDED,
        )


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


class ThreeModelAdapter:
    def create(self, descriptor, config, content):
        if descriptor.verifier != "3d-model-v1" or descriptor.evaluation != "contract-v2-v1":
            raise ValueError("3D Benchmark manifest 组件组合不受支持")
        tasks_path = Path(config["tasks_path"])
        runtime_path = Path(config["runtime_path"])
        if not tasks_path.is_dir() or not runtime_path.is_dir():
            raise ValueError("3D Benchmark 的 tasks_path/runtime_path 必须是目录")
        benchmark = ThreeModelBenchmark(tasks_path)
        return BenchmarkBundle(
            descriptor,
            benchmark.name,
            benchmark,
            ThreeModelVerifier(content, runtime_path),
            ThreeModelEvaluation(),
        )
