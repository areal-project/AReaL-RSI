"""Operator configuration is separate from user intent and agent-editable products."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from areal_rsi.api.control import validate_record
from areal_rsi.api.records import EvolutionTarget
from areal_rsi.api.research import TaskBrief
from areal_rsi.infra.config import ResearchConnection, local_codex_auth, validate_base_url
from areal_rsi.infra.coordination.policies.collaboration import selected_modules
from areal_rsi.infra.execution import ApprovedCommand
from areal_rsi.infra.storage.files import decode, encode

if TYPE_CHECKING:
    from areal_rsi.infra.runtime.runtime_package import RuntimeSettings

MODEL_EFFORTS = {
    "gpt-5.6-luna": ("none", "low", "medium", "high", "xhigh", "max"),
    "gpt-5.6-sol": ("none", "low", "medium", "high", "xhigh", "max"),
    "gpt-5.6-terra": ("none", "low", "medium", "high", "xhigh", "max"),
    "gpt-6-astra": ("low", "medium", "high", "xhigh", "max"),
}
MODEL_FIELDS = ("base_url", "model", "reasoning_effort", "backend", "auth_mode")


def validate_model_settings(value: dict[str, Any]) -> dict[str, str]:
    value = {"backend": "standard", "auth_mode": "api_key", **value}
    if set(value) != set(MODEL_FIELDS) or any(
        not isinstance(value[key], str) or not value[key].strip() for key in MODEL_FIELDS
    ):
        raise ValueError("请填写 API 地址、模型和推理强度")
    settings = {key: value[key].strip() for key in MODEL_FIELDS}
    if settings["auth_mode"] not in {"api_key", "codex_login"}:
        raise ValueError("认证方式支持 api_key 或 codex_login")
    if settings["auth_mode"] == "codex_login":
        if settings["backend"] not in {"codex", "runtime"}:
            raise ValueError("本地 Codex 登录仅支持 Codex 后端")
        settings["base_url"] = "https://api.openai.com"
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
    auth_mode: Literal["api_key", "codex_login"] = "api_key"
    runtime_config: dict[str, Any] | None = None
    model: str = "gpt-6-astra"
    reasoning_effort: str = "medium"
    base_url: str = "https://api.openai.com"
    model_settings_pinned: bool = False
    model_settings_revision: str = ""
    api_key_env: str = "RSI_API_KEY"
    credential_file: str | None = None
    max_output_tokens: int = 16384
    context_window_tokens: int | None = None
    commands: tuple[ApprovedCommand, ...] = ()
    verifier: ApprovedCommand | None = None
    finalizer: ApprovedCommand | None = None
    execution_env: dict[str, str] = field(default_factory=dict)
    simulation_scores: tuple[float, ...] = (0.3, 0.5, 0.7)
    metadata: dict[str, Any] = field(default_factory=dict)
    evolution_target: EvolutionTarget | None = None

    def __post_init__(self) -> None:
        if self.auth_mode not in {"api_key", "codex_login"}:
            raise ValueError("Invalid authentication mode")
        if self.auth_mode == "codex_login":
            if self.backend not in {"codex", "runtime"}:
                raise ValueError("本地 Codex 登录仅支持 Codex 后端")
            self.base_url = "https://api.openai.com"
        if self.evolution_target is not None:
            self.evolution_target = EvolutionTarget(self.evolution_target)
        if self.brief.limits.max_model_calls is not None:
            raise ValueError("max_model_calls is not supported by native runtimes; use null")
        # Old configurations remain decodable for viewing; executable entry points reject them.
        if self.mode not in {"collaborative", "pass1"}:
            self.collaboration_modules = selected_modules(self.mode, self.collaboration_modules)
        if self.backend not in {"standard", "codex", "runtime"}:
            raise ValueError("Agent 后端支持 standard、codex 或 runtime")
        if self.brief.limits.max_effective_seconds is not None and self.mode != "passk":
            raise ValueError("Per-researcher effective time requires passk")
        ResearchConnection(
            self.base_url, self.model, self.reasoning_effort,
            self.max_output_tokens, self.context_window_tokens, self.backend,
        )
        if self.mode == "passk":
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
        from areal_rsi.infra.runtime.runtime_package import RuntimeSettings

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
        if self.auth_mode == "codex_login":
            settings = self.runtime_settings()
            if any(settings.select(role)["kind"] != "codex-cli"
                   for role in ("rsi-agent", "research-agent", "interaction-agent")):
                raise ValueError("本地登录要求所有研究角色使用 Codex Runtime")
            local_codex_auth()
        elif not self.api_key():
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
        if self.auth_mode == "codex_login":
            return ""
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
    if "research_prompt_variant" in value:
        raise ValueError("research_prompt_variant 已移除；Prompt 由协作配置选择")
    unknown = set(value) - ResearchConfig.__dataclass_fields__.keys()
    if unknown:
        raise ValueError("Unknown research configuration fields: " + ", ".join(sorted(unknown)))
    validate_record(ResearchConfig, value)
    selected_modules(value.get("mode", "passk"), value.get("collaboration_modules", ()))
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
    config = config_from_dict(json.loads(path.read_text()))
    if config.seed_dir is not None:
        seed = Path(config.seed_dir).expanduser()
        config.seed_dir = str((path.parent / seed).resolve() if not seed.is_absolute() else seed)
    return config
