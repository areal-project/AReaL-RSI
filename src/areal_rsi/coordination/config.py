"""Operator configuration is separate from user intent and agent-editable products."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from areal_rsi.api.research import TaskBrief
from areal_rsi.base.storage import decode, encode
from areal_rsi.coordination.agent_config import AgentOverrides
from areal_rsi.coordination.benchmark import validate_full_benchmark
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
    # Operator-owned preparation check; never exposed as an agent execution tool.
    preflight: ApprovedCommand | None = None
    verifier: ApprovedCommand | None = None
    finalizer: ApprovedCommand | None = None
    execution_env: dict[str, str] = field(default_factory=dict)
    simulation_scores: tuple[float, ...] = (0.3, 0.5, 0.7)
    metadata: dict[str, Any] = field(default_factory=dict)
    agents: AgentOverrides = field(default_factory=AgentOverrides)

    def __post_init__(self) -> None:
        if "full_baseline_source" in self.metadata and not self.metadata.get(
            "full_benchmark_evaluation"
        ):
            raise ValueError("full_baseline_source requires full_benchmark_evaluation")
        validate_full_benchmark(self.metadata)
        if self.metadata.get("full_benchmark_evaluation"):
            if self.finalizer is not None:
                raise ValueError(
                    "Full benchmark policy requires RSI final verification, not finalizer"
                )
            if self.seed_dir is None or self.verifier is None or self.brief.verification is None:
                raise ValueError(
                    "Full benchmark evaluation requires seed_dir, verifier and contract"
                )
            if (
                self.brief.limits.max_verify_calls is not None
                and self.brief.limits.max_verify_calls
                < (1 if "full_baseline_source" in self.metadata else 2)
            ):
                raise ValueError("Reserve verification budget for full baseline and final")
        self.collaboration_modules = selected_modules(self.mode, self.collaboration_modules)
        if self.backend not in {"standard", "codex"}:
            raise ValueError("Agent 后端支持 standard 或 codex")
        if self.research_prompt_variant not in {"simple", "detailed"}:
            raise ValueError("Research Prompt 支持 simple 或 detailed")
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
            if limits.max_wall_seconds is None and not (
                contract and contract.target is not None and self.verifier
            ):
                raise ValueError("passk needs a wall-time limit or a trusted verification target")

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

    def freeze_agents(self) -> None:
        from areal_rsi.coordination.collaboration import build_plan

        plan = build_plan(self.mode, self.collaboration_modules, self.research_prompt_variant)
        self.agents.freeze({"research": plan.research_prompt, "rsi": plan.rsi_prompt})


def config_from_dict(value: dict[str, Any]) -> ResearchConfig:
    if "api_key" in value:
        raise ValueError("Use api_key_env; credentials must not be saved in research configuration")
    agents = value.get("agents", {})
    if not isinstance(agents, dict) or set(agents) - {"research", "rsi"}:
        raise ValueError("agents currently supports research and rsi")
    for role, method in agents.items():
        if method is not None and (
            not isinstance(method, dict) or set(method) - {"base", "prompt", "skills", "snapshot"}
        ):
            raise ValueError(f"agents.{role} accepts base, prompt and skills; prompt is replaced")
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
    value = yaml.safe_load(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("Research configuration must be a YAML or JSON object")
    config = config_from_dict(value)
    source = config.metadata.get("full_baseline_source")
    if isinstance(source, str) and not source.startswith(("http://", "https://")):
        source_path = Path(source).expanduser()
        config.metadata["full_baseline_source"] = str((path.parent / source_path).resolve())
    if config.seed_dir is not None:
        seed = Path(config.seed_dir).expanduser()
        config.seed_dir = str((path.parent / seed).resolve() if not seed.is_absolute() else seed)
    for role in ("research", "rsi"):
        agent = getattr(config.agents, role)
        if agent is None:
            continue
        for name in ("prompt", "skills"):
            source = getattr(agent, name)
            if source:
                location = Path(source).expanduser()
                setattr(
                    agent,
                    name,
                    str(
                        (path.parent / location).absolute()
                        if not location.is_absolute()
                        else location
                    ),
                )
    config.freeze_agents()
    return config
