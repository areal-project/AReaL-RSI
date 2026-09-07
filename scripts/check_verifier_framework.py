"""End-to-end contract check for loading and executing a user Verifier."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict

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
    VerifierSpec,
)
from areal_rsi.framework import VerifierExecutor, VerifierLoader


class ContractVerifier:
    """Small fixture used only to exercise the public framework contract."""

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        return VerificationResult(
            task_id=request.task.task_id,
            trajectory_id=request.result.trajectory.trajectory_id,
            passed=True,
            score=1.0,
            metrics={"contract": 1.0},
            feedback="framework contract accepted",
        )


async def main() -> None:
    model = ModelVersion(
        version_id="model",
        name="model",
        content=ContentRef("content://model", "sha256:model"),
        created_at="2026-09-05T00:00:00Z",
    )
    harness = HarnessVersion(
        version_id="harness",
        name="harness",
        content=ContentRef("content://harness", "sha256:harness"),
        created_at="2026-09-05T00:00:00Z",
    )
    request = VerificationRequest(
        task=Task("task", {"input": "contract-check"}),
        solution=SolutionVersion(
            solution_id="solution",
            model=model,
            harness=harness,
            created_at="2026-09-05T00:00:00Z",
        ),
        result=TaskResult(
            output=ContentRef("content://output", "sha256:output"),
            trajectory=TrajectoryRef("trajectory"),
        ),
        verifier=VerifierSpec(
            verifier_id="contract-verifier",
            provider="python",
            entrypoint="__main__:ContractVerifier",
            digest="sha256:contract-verifier",
        ),
    )
    verifier = VerifierLoader().create(request.verifier)
    result = await VerifierExecutor().verify(verifier, request)
    print(json.dumps(asdict(result), indent=2))


if __name__ == "__main__":
    asyncio.run(main())
