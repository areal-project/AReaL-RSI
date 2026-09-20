"""小型原创算术 Benchmark，固定题目与 verifier，不宣称是 GSM8K。"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from contextlib import suppress
from decimal import Decimal, InvalidOperation

from areal_rsi.api.records import (
    BenchmarkSplit,
    EvaluationReport,
    SolutionVersion,
    Task,
    VerificationRequest,
    VerificationResult,
    VerificationStatus,
)
from areal_rsi.infra.storage.files import FileContentStore, now

# 只由宿主程序加载，模型工作区只收到题目，不含答案表和 verifier 源码。
_CASES = {
    BenchmarkSplit.TRAIN: (
        ("A bakery makes 48 rolls. It sells 3 boxes of 9 rolls. How many rolls remain?", "21"),
        ("Mira reads 15 pages per day for 6 days, then 22 more pages. How many in total?", "112"),
        ("A bus ticket costs $7. Four friends each buy two tickets. What is the total cost?", "56"),
    ),
    BenchmarkSplit.VALIDATE: (
        (
            "A farm packs 84 eggs equally into cartons of 6. It ships 5 cartons. "
            "How many cartons remain?",
            "9",
        ),
        (
            "A jacket costs $80 and has a 25 percent discount. "
            "How much does it cost after the discount?",
            "60",
        ),
        (
            "A tank holds 150 liters. Each minute 12 liters flow out and 5 flow in. "
            "How many liters remain after 8 minutes?",
            "94",
        ),
    ),
}


class ArithmeticBenchmark:
    name = "arithmetic-v1"

    async def list_tasks(self, split: BenchmarkSplit) -> Sequence[Task]:
        return [
            Task(f"{split.value}_{i}", {"question": question})
            for i, (question, _) in enumerate(_CASES[split])
        ]


class ArithmeticVerifier:
    def __init__(self, content: FileContentStore):
        self.content = content
        self.expected = {
            f"{split.value}_{i}": answer
            for split, rows in _CASES.items()
            for i, (_, answer) in enumerate(rows)
        }

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        task, solution, result = request.task, request.solution, request.result
        output = json.loads(await self.content.read_text(result.output))
        if (
            output.get("solution_id") != solution.solution_id
            or output.get("task_id") != task.task_id
        ):
            raise ValueError("任务产物与评测版本不匹配")
        matches = re.findall(r"####\s*([-+]?\d[\d,]*(?:\.\d+)?)", output.get("response", ""))
        passed = False
        if matches:
            with suppress(InvalidOperation):
                passed = Decimal(matches[-1].replace(",", "")) == Decimal(
                    self.expected[task.task_id]
                )
        feedback = "正确" if passed else "答案数值错误" if matches else "缺少 #### 数字 的最终答案"
        return VerificationResult(
            task.task_id,
            result.trajectory.trajectory_id,
            passed,
            float(passed),
            {"correct": float(passed)},
            feedback,
        )


class AccuracyEvaluation:
    async def evaluate(
        self,
        solution: SolutionVersion,
        split: BenchmarkSplit,
        results: Sequence[VerificationResult],
        *,
        baseline: EvaluationReport | None = None,
    ) -> EvaluationReport:
        if not results or len({r.task_id for r in results}) != len(results):
            raise ValueError("评测必须包含完整且不重复的任务结果")
        if any(r.status is not VerificationStatus.SUCCEEDED for r in results):
            raise ValueError("Verifier 未成功完成，不能聚合为任务分数")
        scores = [r.score for r in results]
        passed = [r.passed for r in results]
        if any(value is None or not math.isfinite(value) for value in scores):
            raise ValueError("缺失或非有限评测值")
        if any(value is None for value in passed):
            raise ValueError("缺失任务验证结论")
        valid_passed = [value for value in passed if value is not None]
        score = sum(valid_passed) / len(valid_passed)
        return EvaluationReport(
            solution.solution_id,
            split,
            all(valid_passed),
            score,
            {
                "accuracy": score,
                "task_count": float(len(results)),
            },
            now(),
        )
