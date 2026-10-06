"""AReaL TIR workflow with worker-side RSI source and snapshot attestation."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import areal  # type: ignore[import-not-found]
from examples.tir import tir_workflow  # type: ignore[import-not-found]
from examples.tir.tir_workflow import TIRWorkflow  # type: ignore[import-not-found]

from areal_rsi.infra.coordination.evaluation.assets import (
    attest_worker_inputs,
    validate_areal_import_root,
)
from areal_rsi.infra.storage.artifacts import checkpoint_digest


@lru_cache(maxsize=8)
def _attest_once(
    task_set_root: str,
    harness_root: str,
    model_root: str,
    task_set_id: str,
    harness_id: str,
    runner_digest: str,
    verifier_digest: str,
    model_digest: str,
    prompt_template: str,
) -> None:
    runner_root = Path(tir_workflow.__file__).resolve().parent
    validate_areal_import_root(runner_root, Path(areal.__file__))
    attest_worker_inputs(
        Path(task_set_root),
        Path(harness_root),
        runner_root,
        task_set_id=task_set_id,
        harness_id=harness_id,
        runner_digest=runner_digest,
        verifier_digest=verifier_digest,
        prompt_template=prompt_template,
    )
    if checkpoint_digest(Path(model_root)) != model_digest:
        raise ValueError("Slurm worker model snapshot differs from controller")


class FrozenTIRWorkflow(TIRWorkflow):  # type: ignore[misc]
    def __init__(
        self,
        *,
        task_set_root: str,
        harness_root: str,
        model_root: str,
        task_set_id: str,
        harness_id: str,
        runner_digest: str,
        verifier_digest: str,
        model_digest: str,
        prompt_template: str,
        **workflow_kwargs: Any,
    ) -> None:
        _attest_once(
            task_set_root,
            harness_root,
            model_root,
            task_set_id,
            harness_id,
            runner_digest,
            verifier_digest,
            model_digest,
            prompt_template,
        )
        super().__init__(prompt_template=prompt_template, **workflow_kwargs)
