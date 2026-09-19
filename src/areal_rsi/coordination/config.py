"""Operator configuration is separate from user intent and agent-editable products."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from areal_rsi.api.research import TaskBrief
from areal_rsi.base.storage import decode, encode
from areal_rsi.coordination.collaboration import selected_modules
from areal_rsi.execution import ApprovedCommand
from areal_rsi.modules.responses import ResponsesConfig

MODEL_EFFORTS = {
    "gpt-5.6-luna": ("none", "low", "medium", "high", "xhigh", "max"),
    "gpt-5.6-sol": ("none", "low", "medium", "high", "xhigh", "max"),
    "gpt-5.6-terra": ("none", "low", "medium", "high", "xhigh", "max"),
    "gpt-6-astra": ("low", "medium", "high", "xhigh", "max"),
}
MODEL_FIELDS = ("base_url", "model", "reasoning_effort", "backend")


def validate_model_settings(value: dict[str, Any]) -> dict[str, str]:
    value = {"backend": "standard", **value}
    if set(value) != set(MODEL_FIELDS) or any(
        not isinstance(value[key], str) or not value[key].strip() for key in MODEL_FIELDS
    ):
        raise ValueError("请填写 API 地址、模型和推理强度")
    settings = {key: value[key].strip() for key in MODEL_FIELDS}
    if settings["backend"] not in {"standard", "codex"}:
        raise ValueError("Agent 后端支持 standard 或 codex")
    if len(settings["model"]) > 200 or any(c.isspace() for c in settings["model"]):
        raise ValueError("模型名称不能包含空格，且不能超过 200 字")
    if settings["reasoning_effort"] not in MODEL_EFFORTS.get(
        settings["model"], ("none", "low", "medium", "high", "xhigh", "max")
    ):
        raise ValueError("该模型不支持所选推理强度")
    # Reuse the actual transport's URL validation; no request or credential is needed.
    try:
        ResponsesConfig(
            api_key="validation-only", **{k: v for k, v in settings.items() if k != "backend"}
        )
        if any(char.isspace() for char in settings["base_url"]):
            raise ValueError
    except ValueError as exc:
        raise ValueError("API 地址须为不含密钥、查询参数或空格的 HTTP(S) 地址") from exc
    settings["base_url"] = settings["base_url"].rstrip("/")
    return settings


@dataclass(slots=True)
class ResearchConfig:
    brief: TaskBrief
    mode: str = "collaborative"
    collaboration_modules: tuple[str, ...] = ()
    seed_dir: str | None = None
    backend: str = "standard"
    research_prompt_variant: str = "simple"
    model: str = "gpt-5.6-luna"
    reasoning_effort: str = "xhigh"
    base_url: str = "https://api.openai.com"
    model_settings_pinned: bool = False
    model_settings_revision: str = ""
    api_key_env: str = "RSI_API_KEY"
    credential_file: str | None = None
    max_output_tokens: int = 16384
    context_window_tokens: int | None = None
    compact_keep_recent_tokens: int = 8_000
    max_model_calls_per_turn: int | None = None
    retry_initial_seconds: float = 2
    retry_max_seconds: float = 60
    commands: tuple[ApprovedCommand, ...] = ()
    verifier: ApprovedCommand | None = None
    finalizer: ApprovedCommand | None = None
    execution_env: dict[str, str] = field(default_factory=dict)
    simulation_scores: tuple[float, ...] = (0.3, 0.5, 0.7)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.collaboration_modules = selected_modules(self.mode, self.collaboration_modules)
        if self.backend not in {"standard", "codex"}:
            raise ValueError("Agent 后端支持 standard 或 codex")
        if self.research_prompt_variant not in {"simple", "detailed"}:
            raise ValueError("Research Prompt 支持 simple 或 detailed")
        if self.brief.limits.max_effective_seconds is not None and self.mode != "passk":
            raise ValueError("Per-researcher effective time requires passk")
        if self.mode == "passk":
            limits = self.brief.limits
            if self.max_model_calls_per_turn is not None:
                raise ValueError("passk requires max_model_calls_per_turn to be null")
            if any(
                getattr(limits, name) is not None
                for name in (
                    "max_model_calls",
                    "max_total_tokens",
                    "max_verify_calls",
                )
            ):
                raise ValueError(
                    "passk requires unlimited model/token/verification counts (null); "
                    "use a wall-time limit"
                )
            if limits.max_nodes is not None and limits.max_nodes < limits.max_agents:
                raise ValueError("passk max_nodes must accommodate all k researchers")
            contract = self.brief.verification
            if (
                limits.max_wall_seconds is None
                and limits.max_effective_seconds is None
                and not (contract and contract.target is not None and self.verifier)
            ):
                raise ValueError(
                    "passk needs a wall-time/effective-time limit or a trusted verification target"
                )

    def model_settings(self) -> dict[str, str]:
        return {
            key: str(getattr(self, key))
            if self.model_settings_pinned
            else os.environ.get("RSI_" + key.upper(), str(getattr(self, key)))
            for key in MODEL_FIELDS
        }

    def pin_model_settings(self) -> None:
        for key, value in self.model_settings().items():
            setattr(self, key, value)
        self.model_settings_pinned = True
        self.__post_init__()

    def runtime(self) -> ResponsesConfig:
        key = self.api_key()
        if not key:
            raise ValueError(f"Set {self.api_key_env} before starting model agents")
        settings = self.model_settings()
        return ResponsesConfig(
            api_key=key,
            base_url=settings["base_url"],
            model=settings["model"],
            reasoning_effort=settings["reasoning_effort"],
            max_output_tokens=self.max_output_tokens,
            context_window_tokens=self.context_window_tokens,
            compact_keep_recent_tokens=self.compact_keep_recent_tokens,
            max_model_calls_per_turn=self.max_model_calls_per_turn,
            retry_initial_seconds=self.retry_initial_seconds,
            retry_max_seconds=self.retry_max_seconds,
        )

    def api_key(self) -> str:
        if self.credential_file:
            return str(json.loads(Path(self.credential_file).read_text())["api_key"])
        return os.environ.get(self.api_key_env, "")

    def public(self) -> dict[str, Any]:
        """Configuration is useful for recovery, but not a public web projection."""
        result: dict[str, Any] = encode(self)
        return result


def config_from_dict(value: dict[str, Any]) -> ResearchConfig:
    if "api_key" in value:
        raise ValueError("Use api_key_env; credentials must not be saved in research configuration")
    config: ResearchConfig = decode(ResearchConfig, value)
    if config.metadata.get("time_limit_scope", "total") not in {"total", "exploration"}:
        raise ValueError("time_limit_scope must be total or exploration")
    names = [command.name for command in config.commands]
    if len(set(names)) != len(names):
        raise ValueError("Execution command names must be unique")
    if config.verifier is None and any(
        command.auto_verify_output is not None for command in config.commands
    ):
        raise ValueError("Automatic artifact verification requires a trusted verifier")
    if config.finalizer and (config.verifier is None or config.finalizer.parameters != ("output",)):
        raise ValueError("Finalizer requires a verifier and exactly one output parameter")
    return config


def load_config(path: Path) -> ResearchConfig:
    config = config_from_dict(json.loads(path.read_text()))
    if config.seed_dir is not None:
        seed = Path(config.seed_dir).expanduser()
        config.seed_dir = str((path.parent / seed).resolve() if not seed.is_absolute() else seed)
    return config
