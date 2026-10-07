"""Frozen configuration for the shared prompt RSI cycle and AReaL recipes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from areal_rsi.api.control import read_record
from areal_rsi.infra.coordination.evaluation.assets import (
    RSI_REWARD_MAX_RETRIES,
    RSI_REWARD_TIMEOUT_SECONDS,
)


@dataclass(frozen=True, slots=True)
class PromptProfile:
    config_class: str
    workflow: str
    reward_fn: str
    verify_fn: str
    runner_directory: str
    boxed_answers: bool
    enabled_tools: tuple[str, ...]
    researcher_instruction: str

    def __post_init__(self) -> None:
        if self.runner_directory not in ("areal", "examples/tir"):
            raise ValueError("Profile must pin the AReaL package or its TIR workflow")
        if self.enabled_tools not in ((), ("calculator",)):
            raise ValueError("Prompt profiles support plain RLVR or the calculator workflow")
        for path in (self.config_class, self.workflow, self.reward_fn, self.verify_fn):
            if "." not in path or not all(part.isidentifier() for part in path.split(".")):
                raise ValueError("Profile requires importable config, workflow and reward paths")
        if not self.researcher_instruction.strip():
            raise ValueError("Profile needs a task-specific researcher instruction")


def load_profile(path: Path) -> PromptProfile:
    return read_record(path, PromptProfile)


def workflow_kwargs(
    profile: PromptProfile,
    config: Any,
    *,
    prompt_template: str,
    task_set_root: Path,
    harness_root: Path,
    task_set_id: str,
    harness_id: str,
    runner_digest: str,
    verifier_digest: str,
    model_digest: str,
) -> dict[str, Any]:
    # Serialized kwargs are an AReaL plugin boundary; both train and eval share this payload.
    result = {
        "reward_fn": profile.reward_fn,
        "gconfig": config.gconfig,
        "tokenizer": config.tokenizer_path,
        "enable_thinking": False,
        "prompt_template": prompt_template,
        "task_set_root": str(task_set_root.resolve()),
        "harness_root": str(harness_root.resolve()),
        "model_root": str(Path(config.actor.path).resolve()),
        "task_set_id": task_set_id,
        "harness_id": harness_id,
        "runner_digest": runner_digest,
        "verifier_digest": verifier_digest,
        "model_digest": model_digest,
    }
    if profile.enabled_tools:
        # One calculator episode must export one complete trajectory for paired evaluation.
        config.rollout.agent.chat_template_type = "concat"
        config.rollout.agent.export_style = "concat"
        result.update(
            tir_config=config.tir,
            fail_on_reward_timeout=True,
            reward_timeout_seconds=RSI_REWARD_TIMEOUT_SECONDS,
            reward_max_retries=RSI_REWARD_MAX_RETRIES,
        )
    return result
