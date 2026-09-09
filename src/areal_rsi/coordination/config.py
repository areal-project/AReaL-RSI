"""Operator configuration is separate from user intent and agent-editable products."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from areal_rsi.api.research import TaskBrief
from areal_rsi.base.storage import decode, encode
from areal_rsi.execution import ApprovedCommand
from areal_rsi.modules.responses import ResponsesConfig


@dataclass(slots=True)
class ResearchConfig:
    brief: TaskBrief
    seed_dir: str | None = None
    model: str = "gpt-6-astra"
    reasoning_effort: str = "xhigh"
    base_url: str = "https://api.openai.com"
    api_key_env: str = "RSI_API_KEY"
    max_output_tokens: int = 16384
    max_model_calls_per_turn: int = 32
    commands: tuple[ApprovedCommand, ...] = ()
    verifier: ApprovedCommand | None = None
    execution_env: dict[str, str] = field(default_factory=dict)
    simulation_scores: tuple[float, ...] = (0.3, 0.5, 0.7)
    metadata: dict[str, Any] = field(default_factory=dict)

    def runtime(self) -> ResponsesConfig:
        key = os.environ.get(self.api_key_env, "")
        if not key:
            raise ValueError(f"Set {self.api_key_env} before starting model agents")
        return ResponsesConfig(
            base_url=os.environ.get("RSI_BASE_URL", self.base_url),
            api_key=key,
            model=os.environ.get("RSI_MODEL", self.model),
            reasoning_effort=os.environ.get("RSI_REASONING_EFFORT", self.reasoning_effort),
            max_output_tokens=self.max_output_tokens,
            max_model_calls_per_turn=self.max_model_calls_per_turn,
        )

    def public(self) -> dict[str, Any]:
        """Configuration is useful for recovery, but not a public web projection."""
        result: dict[str, Any] = encode(self)
        return result


def load_config(path: Path) -> ResearchConfig:
    value = json.loads(path.read_text())
    if "api_key" in value:
        raise ValueError("Use api_key_env; credentials must not be saved in research configuration")
    config: ResearchConfig = decode(ResearchConfig, value)
    if config.seed_dir is not None:
        seed = Path(config.seed_dir).expanduser()
        config.seed_dir = str((path.parent / seed).resolve() if not seed.is_absolute() else seed)
    names = [command.name for command in config.commands]
    if len(set(names)) != len(names):
        raise ValueError("Execution command names must be unique")
    return config
