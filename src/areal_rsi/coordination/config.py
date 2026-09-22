"""Operator configuration is separate from user intent and agent-editable products."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml  # type: ignore[import-untyped]

from areal_rsi.api.base import EvolutionTarget
from areal_rsi.api.research import TaskBrief
from areal_rsi.base.storage import decode, encode
from areal_rsi.coordination.agent_config import AgentOverrides
from areal_rsi.coordination.benchmark import validate_full_benchmark
from areal_rsi.coordination.collaboration import selected_modules
from areal_rsi.execution import ApprovedCommand
from areal_rsi.modules.research_settings import ResearchConnection, validate_base_url

if TYPE_CHECKING:
    from areal_rsi.modules.runtime_package import RuntimeSettings

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
    if settings["backend"] not in {"standard", "codex", "runtime"}:
        raise ValueError("Agent 后端支持 standard、codex 或 runtime")
    if len(settings["model"]) > 200 or any(c.isspace() for c in settings["model"]):
        raise ValueError("模型名称不能包含空格，且不能超过 200 字")
    if settings["reasoning_effort"] not in MODEL_EFFORTS.get(
        settings["model"], ("none", "low", "medium", "high", "xhigh", "max")
    ):
        raise ValueError("该模型不支持所选推理强度")
    settings["base_url"] = validate_base_url(settings["base_url"])
    return settings


@dataclass(slots=True)
class ResearchConfig:
    brief: TaskBrief
    mode: str = "passk"
    collaboration_modules: tuple[str, ...] = ()
    seed_dir: str | None = None
    backend: str = "standard"
    runtime_config: dict[str, Any] | None = None
    model: str = "gpt-5.6-luna"
    reasoning_effort: str = "xhigh"
    base_url: str = "https://api.openai.com"
    model_settings_pinned: bool = False
    model_settings_revision: str = ""
    api_key_env: str = "RSI_API_KEY"
    credential_file: str | None = None
    max_output_tokens: int = 16384
    context_window_tokens: int | None = None
    commands: tuple[ApprovedCommand, ...] = ()
    # Operator-owned preparation check; never exposed as an agent execution tool.
    preflight: ApprovedCommand | None = None
    verifier: ApprovedCommand | None = None
    finalizer: ApprovedCommand | None = None
    execution_env: dict[str, str] = field(default_factory=dict)
    simulation_scores: tuple[float, ...] = (0.3, 0.5, 0.7)
    metadata: dict[str, Any] = field(default_factory=dict)
    agents: AgentOverrides = field(default_factory=AgentOverrides)
    evolution_target: EvolutionTarget | None = None

    def __post_init__(self) -> None:
        if self.evolution_target is not None:
            self.evolution_target = EvolutionTarget(self.evolution_target)
        if self.brief.limits.max_model_calls is not None:
            raise ValueError("max_model_calls is not supported by native runtimes; use null")
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
        # Old configurations remain decodable for viewing; executable entry points reject them.
        if self.mode != "collaborative":
            self.collaboration_modules = selected_modules(self.mode, self.collaboration_modules)
        if self.backend not in {"standard", "codex", "runtime"}:
            raise ValueError("Agent 后端支持 standard、codex 或 runtime")
        if self.brief.limits.max_effective_seconds is not None and self.mode != "passk":
            raise ValueError("Per-researcher effective time requires passk")
        ResearchConnection(
            self.base_url, self.model, self.reasoning_effort,
            self.max_output_tokens, self.context_window_tokens, self.backend,
        )
        if self.mode == "passk" and not self.metadata.get("full_benchmark_evaluation"):
            limits = self.brief.limits
            if any(
                getattr(limits, name) is not None
                for name in (
                    "max_total_tokens",
                    "max_verify_calls",
                )
            ):
                raise ValueError(
                    "passk requires unlimited token/verification counts (null); "
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
        settings = {
            key: str(getattr(self, key))
            if self.model_settings_pinned
            else os.environ.get("RSI_" + key.upper(), str(getattr(self, key)))
            for key in MODEL_FIELDS
        }

        if not self.model_settings_pinned and "RSI_BACKEND" not in os.environ and (
            self.runtime_config is not None or os.environ.get("RSI_RUNTIME_CONFIG")
        ):
            settings["backend"] = "runtime"
        return settings

    def runtime_settings(self) -> RuntimeSettings:
        from areal_rsi.modules.runtime_package import RuntimeSettings

        if self.backend != "runtime":
            return RuntimeSettings({"default": {
                "kind": "codex-cli" if self.backend == "codex" else "dsh",
                "config": {"modelProtocol": "responses", "timeoutSeconds": 86400}
                if self.backend == "codex" else {"reasoningEffort": self.reasoning_effort},
            }})
        if self.runtime_config is None:
            path = os.environ.get("RSI_RUNTIME_CONFIG")
            if not path:
                raise ValueError("runtime backend requires runtime_config or RSI_RUNTIME_CONFIG")
            self.runtime_config = json.loads(Path(path).expanduser().read_text())
        settings = RuntimeSettings(self.runtime_config)
        for role in ("research-agent", "rsi-agent", "interaction-agent"):
            kind = settings.select(role)["kind"]
            ResearchConnection(
                self.base_url, self.model, self.reasoning_effort,
                self.max_output_tokens, self.context_window_tokens,
                "codex" if kind == "codex-cli" else "standard",
            )
        return settings

    def pin_model_settings(self) -> None:
        for key, value in self.model_settings().items():
            setattr(self, key, value)
        self.runtime_settings()
        self.model_settings_pinned = True
        self.__post_init__()

    def runtime(self) -> ResearchConnection:
        connection = self.connection_settings()
        if not self.api_key():
            raise ValueError(f"Set {self.api_key_env} before starting model agents")
        return connection

    def connection_settings(self) -> ResearchConnection:
        """Validate effective native settings without requiring credentials for a draft."""
        settings = validate_model_settings(self.model_settings())
        return ResearchConnection(
            base_url=settings["base_url"],
            model=settings["model"],
            reasoning_effort=settings["reasoning_effort"],
            max_output_tokens=self.max_output_tokens,
            context_window_tokens=self.context_window_tokens,
            backend=settings["backend"],
        )

    def validate_clock(self) -> None:
        if (
            self.brief.limits.max_effective_seconds is not None
            and self.runtime_settings().select("research-agent")["kind"] != "codex-cli"
        ):
            raise ValueError("有效时间目前仅支持 Codex；standard 请使用 max_wall_seconds")

    @property
    def needs_finalization(self) -> bool:
        contract = self.brief.verification
        return bool(
            self.finalizer
            or (self.verifier and contract and contract.identity.get("require_final_confirmation"))
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

        plan = build_plan(self.mode, self.collaboration_modules)
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
    selected_modules(value.get("mode", "passk"), value.get("collaboration_modules", ()))
    if "research_prompt_variant" in value:
        raise ValueError("research_prompt_variant 已移除；Prompt 由协作配置选择")
    unknown = set(value) - ResearchConfig.__dataclass_fields__.keys()
    if unknown:
        raise ValueError("Unknown research configuration fields: " + ", ".join(sorted(unknown)))
    try:
        config: ResearchConfig = decode(ResearchConfig, value)
    except KeyError as exc:
        raise ValueError(f"Unknown research configuration field: {exc.args[0]}") from None
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


def archived_config(value: dict[str, Any]) -> ResearchConfig:
    """Read historical configuration without rewriting its mode, prompts, or budgets."""
    value = dict(value)
    value.pop("research_prompt_variant", None)
    value.setdefault("mode", "collaborative")
    config: ResearchConfig = decode(ResearchConfig, value)
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
