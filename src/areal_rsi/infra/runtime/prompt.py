"""Frozen prompt workflow using AReaL's native single-turn RLVR implementation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from areal.workflow.rlvr import RLVRWorkflow  # type: ignore[import-not-found]

from areal_rsi.infra.coordination.evaluation.assets import (
    attest_worker_inputs,
    validate_areal_import_root,
)
from areal_rsi.infra.storage.artifacts import checkpoint_digest
from areal_rsi.scripts.train import areal_root


class FrozenPromptWorkflow(RLVRWorkflow):  # type: ignore[misc]
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
        runner = areal_root() / "areal"
        validate_areal_import_root(runner, runner / "__init__.py")
        attest_worker_inputs(
            Path(task_set_root),
            Path(harness_root),
            runner,
            task_set_id=task_set_id,
            harness_id=harness_id,
            runner_digest=runner_digest,
            verifier_digest=verifier_digest,
            prompt_template=prompt_template,
        )
        if checkpoint_digest(Path(model_root)) != model_digest:
            raise ValueError("Slurm worker model snapshot differs from controller")

        def input_ids(data: dict[str, Any], tokenizer: Any, enable_thinking: bool) -> list[int]:
            prompt = prompt_template.format(prompt=data["messages"][0]["content"])
            return cast(list[int], tokenizer.encode(prompt))

        super().__init__(get_input_ids_fn=input_ids, **workflow_kwargs)
