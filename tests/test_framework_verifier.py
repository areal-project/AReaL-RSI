"""Contract tests for loading and executing user Verifiers."""

from __future__ import annotations

import asyncio
import unittest

from areal_rsi.api.base import (
    ContentRef,
    HarnessVersion,
    ModelVersion,
    SolutionVersion,
    Task,
    TaskResult,
    TrajectoryRef,
    VerificationRequest,
    VerificationResult,
    VerificationStatus,
    VerifierSpec,
)
from areal_rsi.framework.verifier import VerifierExecutor, VerifierLoader


class _PassingVerifier:
    async def verify(self, request: VerificationRequest) -> VerificationResult:
        return VerificationResult(
            task_id=request.task.task_id,
            trajectory_id=request.result.trajectory.trajectory_id,
            passed=True,
            score=2.5,
            metrics={"quality": 2.5},
            feedback="accepted",
        )


class _FailingVerifier:
    async def verify(self, request: VerificationRequest) -> VerificationResult:
        raise RuntimeError("sandbox unavailable")


class _SlowVerifier:
    async def verify(self, request: VerificationRequest) -> VerificationResult:
        await asyncio.sleep(60)
        raise AssertionError("unreachable")


class VerifierFrameworkTest(unittest.IsolatedAsyncioTestCase):
    async def test_python_loader_and_executor_preserve_user_score(self) -> None:
        request = self._request("_PassingVerifier")
        verifier = VerifierLoader().create(request.verifier)

        result = await VerifierExecutor().verify(verifier, request)

        self.assertEqual(result.status, VerificationStatus.SUCCEEDED)
        self.assertTrue(result.passed)
        self.assertEqual(result.score, 2.5)
        self.assertEqual(result.verifier_id, "test-verifier")

    async def test_executor_separates_verifier_failure_from_task_failure(self) -> None:
        request = self._request("_FailingVerifier")
        verifier = VerifierLoader().create(request.verifier)

        result = await VerifierExecutor().verify(verifier, request)

        self.assertEqual(result.status, VerificationStatus.INFRA_ERROR)
        self.assertIsNone(result.passed)
        self.assertIsNone(result.score)
        assert result.error is not None
        self.assertEqual(result.error.code, "verifier_execution_failed")

    async def test_executor_applies_configured_timeout(self) -> None:
        request = self._request("_SlowVerifier", timeout_seconds=0.001)
        verifier = VerifierLoader().create(request.verifier)

        result = await VerifierExecutor().verify(verifier, request)

        self.assertEqual(result.status, VerificationStatus.TIMEOUT)
        self.assertIsNone(result.passed)
        assert result.error is not None
        self.assertEqual(result.error.code, "verifier_timeout")

    @classmethod
    def _request(
        cls,
        verifier_name: str,
        *,
        timeout_seconds: float | None = None,
    ) -> VerificationRequest:
        return VerificationRequest(
            task=Task(task_id="task_1", input={"prompt": "question"}),
            solution=SolutionVersion(
                solution_id="solution_1",
                model=ModelVersion(
                    version_id="model_1",
                    name="model",
                    content=ContentRef("content://model", "sha256:model"),
                    created_at="2026-09-05T00:00:00Z",
                ),
                harness=HarnessVersion(
                    version_id="harness_1",
                    name="harness",
                    content=ContentRef("content://harness", "sha256:harness"),
                    created_at="2026-09-05T00:00:00Z",
                ),
                created_at="2026-09-05T00:00:00Z",
            ),
            result=TaskResult(
                output=ContentRef("content://output", "sha256:output"),
                trajectory=TrajectoryRef("trajectory_1"),
            ),
            verifier=VerifierSpec(
                verifier_id="test-verifier",
                provider="python",
                entrypoint=f"{__name__}:{verifier_name}",
                digest="sha256:verifier",
                timeout_seconds=timeout_seconds,
            ),
        )


if __name__ == "__main__":
    unittest.main()
