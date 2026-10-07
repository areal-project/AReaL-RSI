"""AReaL TIR workflow with worker-side RSI source and snapshot attestation."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import areal  # type: ignore[import-not-found]
from examples.tir import tir_workflow  # type: ignore[import-not-found]
from examples.tir.prompts import has_complete_boxed_answer  # type: ignore[import-not-found]
from examples.tir.tool_manager import ToolManager  # type: ignore[import-not-found]

from areal_rsi.infra.coordination.evaluation.assets import (
    attest_worker_inputs,
    validate_areal_import_root,
)
from areal_rsi.infra.runtime.prompt import PromptRewardAgent
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


class FrozenTIRWorkflow(PromptRewardAgent):
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
        tir_config: Any,
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
        if tir_config.is_chat_model:
            raise ValueError("Frozen TIR prompt templates require non-chat mode")
        self.tir_config = tir_config
        super().__init__(prompt_template=prompt_template, **workflow_kwargs)

    async def run(self, data: dict[str, Any], **extra_kwargs: Any) -> float:
        client = self.client(extra_kwargs)
        messages = self.messages(data)
        original_messages = [dict(message) for message in messages]
        generated: list[str] = []
        tools = ToolManager(self.tir_config.tool_timeout, self.tir_config.enable_tools)
        tool_calls = 0
        pending_tool = ""
        try:
            while tool_calls <= self.tir_config.max_turns:
                prompt = "".join(message["content"] for message in messages)
                maximum = min(self.tir_config.max_length, self.gconfig.max_tokens)
                if len(self.tokenizer.encode(prompt)) >= maximum:
                    break
                completion = await self.generate(
                    client, messages, stop=tools.get_all_end_markers(), max_total_tokens=maximum
                )
                response = completion.choices[0].message.content or ""
                if not response:
                    break
                generated.append(response)
                messages.append({"role": "assistant", "content": response})
                if has_complete_boxed_answer("".join(generated)):
                    break
                # SGLang may omit the stop string from the response text.
                pending_tool += response
                for marker in tools.get_all_end_markers():
                    if (
                        completion.choices[0].finish_reason == "stop"
                        and marker.replace("</", "<") in pending_tool
                        and not pending_tool.endswith(marker)
                    ):
                        pending_tool += marker
                result, status = await tools.aexecute_tool_call(pending_tool)
                if status is tir_workflow.ToolCallStatus.NOT_FOUND:
                    if completion.choices[0].finish_reason != "length":
                        break
                    continue
                tool_calls += 1
                pending_tool = ""
                messages.append({"role": "user", "content": f"\n```output\n{result}\n```\n"})
            return await self.score(
                original_messages,
                "".join(generated),
                data,
                tool_using=tool_calls > 0,
                tool_status=tool_calls,
            )
        finally:
            await tools.acleanup()
