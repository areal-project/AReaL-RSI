"""Disjoint, seeded exact arithmetic tasks with a strict structured-output contract."""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Sequence

from areal_rsi.api.base import BenchmarkSplit, Task, VerificationRequest, VerificationResult
from areal_rsi.base.storage import FileContentStore


class StructuredArithmeticBenchmark:
    name = "structured-arithmetic-v1"

    def __init__(
        self, *, seed: int = 20261015, exact_division: bool = False, rational_terms: bool = False
    ):
        if rational_terms:
            self.name = "structured-arithmetic-v3"
        elif exact_division:
            self.name = "structured-arithmetic-v2"
        self.expected: dict[str, tuple[str, int]] = {}
        self.partitions: dict[BenchmarkSplit, list[Task]] = {}
        for split, count in (
            (BenchmarkSplit.TRAIN, 30),
            (BenchmarkSplit.VALIDATE, 30),
            (BenchmarkSplit.FINAL, 60),
        ):
            rng = random.Random(f"{seed}/{split.value}")
            tasks = []
            for index in range(count):
                a, b, c, d = (rng.randrange(10**12, 10**15) for _ in range(4))
                expression = f"({a} * {b} - {c}) * {d} + {a}"
                answer = (a * b - c) * d + a
                if exact_division:
                    divisor = rng.randrange(7, 997)
                    remainder = (a * b - c) % divisor
                    numerator = a * b - c - remainder
                    expression = f"(({a} * {b} - {c} - {remainder}) / {divisor}) * {d} + {a}"
                    answer = (numerator // divisor) * d + a
                if rational_terms:
                    extra = rng.randrange(10**12, 10**15)
                    expression = f"(({a} / {b}) + ({c} / {d})) * ({b} * {d}) + {extra}"
                    answer = a * d + c * b + extra
                identifier = f"exact-{seed}-{split.value}-{index}"
                checksum = answer % 97
                self.expected[identifier] = str(answer), checksum
                tasks.append(
                    Task(
                        identifier,
                        {
                            "question": "Compute this integer expression exactly: " + expression,
                            "output_contract": (
                                'Return ONLY a JSON object with keys "answer" '
                                '(decimal integer string) and "checksum" '
                                "(integer equal to answer modulo 97). No markdown."
                            ),
                        },
                    )
                )
            self.partitions[split] = tasks
        public = json.dumps(
            {s.value: [t.input for t in tasks] for s, tasks in self.partitions.items()},
            sort_keys=True,
        )
        self.digest = hashlib.sha256(public.encode()).hexdigest()

    async def list_tasks(self, split: BenchmarkSplit) -> Sequence[Task]:
        return self.partitions[split]


class StructuredArithmeticVerifier:
    def __init__(self, content: FileContentStore, benchmark: StructuredArithmeticBenchmark):
        self.content, self.expected = content, benchmark.expected

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        output = json.loads(await self.content.read_text(request.result.output))
        if (
            output["solution_id"] != request.solution.solution_id
            or output["task_id"] != request.task.task_id
        ):
            raise ValueError("Arithmetic result identity mismatch")
        structured, correct = False, False
        try:
            value = json.loads(output["response"])
            structured = (
                isinstance(value, dict)
                and set(value) == {"answer", "checksum"}
                and isinstance(value["answer"], str)
                and type(value["checksum"]) is int
            )
            answer, checksum = self.expected[request.task.task_id]
            correct = structured and value["answer"] == answer and value["checksum"] == checksum
        except (ValueError, KeyError, TypeError):
            pass
        return VerificationResult(
            request.task.task_id,
            request.result.trajectory.trajectory_id,
            correct,
            float(correct),
            {"accuracy": float(correct), "structured_output": float(structured)},
            "Correct" if correct else "Check exact arithmetic and JSON output contract",
        )
