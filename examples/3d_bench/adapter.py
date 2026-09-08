"""Minimal Contract V2 benchmark implementation for one 3D task."""

from __future__ import annotations

import asyncio
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
from areal_rsi.base.storage import DIRECTORY_TYPE, FileContentStore, now
from areal_rsi.benchmarks.catalog import BenchmarkBundle


class ThreeModelBenchmark:
    name = "3d-model-v1"

    def __init__(self, tasks_path: Path, task_id: str = "MODEL-001") -> None:
        self.tasks_path = tasks_path
        self.task_id = task_id
        self.target_path = tasks_path / task_id.removeprefix("MODEL-")
        matches = list(tasks_path.glob(f"{task_id[6:]}-*"))
        if len(matches) != 1:
            raise ValueError(f"无法唯一定位 {task_id} TARGET.md: {tasks_path}")
        self.target_path = matches[0] / "TARGET.md"
        if not self.target_path.is_file():
            raise ValueError(f"缺少 TARGET.md: {self.target_path}")

    async def list_tasks(self, split: BenchmarkSplit) -> Sequence[Task]:
        if split is not BenchmarkSplit.TRAIN:
            return ()
        return (Task(self.task_id, self.target_path.read_text(encoding="utf-8")),)


class ThreeModelVerifier:
    def __init__(self, content: FileContentStore, runtime_path: Path) -> None:
        self.content = content
        self.runtime_path = runtime_path

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        result = request.result
        if result.output.media_type != DIRECTORY_TYPE:
            raise ValueError("3D verifier 要求目录型 TaskResult.output")
        artifact = await self.content.resolve_path(result.output)
        source = artifact / "src" / "model.js"
        if not source.is_file():
            raise ValueError("3D artifact 缺少 src/model.js")
        with tempfile.TemporaryDirectory(prefix="rsi-3d-verify-") as temporary:
            workspace = Path(temporary) / "viewer"
            shutil.copytree(self.runtime_path / "viewer-template", workspace)
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
        if len(results) != 1 or results[0].status is not VerificationStatus.SUCCEEDED:
            raise ValueError("MODEL-001 评测必须包含一个成功完成的验证结果")
        score = float(results[0].score or 0.0)
        return EvaluationReport(
            solution.solution_id, split, score == 1.0, score,
            {"contract_v2": score, "task_count": 1.0}, now(),
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
            descriptor, benchmark.name, benchmark,
            ThreeModelVerifier(content, runtime_path), ThreeModelEvaluation(),
        )
