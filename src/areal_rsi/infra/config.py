"""研究模型与独立 Task 模型的连接设置，共用地址校验。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit

from areal_rsi.api.records import JsonObject


class ModelConfigurationError(ValueError):
    """模型连接缺失或无效，可以直接向用户展示的配置错误。"""


@dataclass(frozen=True)
class ModelConfig:
    model: str
    base_url: str
    api_key: str = field(repr=False)

    @classmethod
    def from_environment(cls, role: Literal["target"] = "target") -> ModelConfig:
        prefix = "RSI_TARGET_"
        model = os.environ.get(prefix + "MODEL", "").strip()
        base_url = os.environ.get(prefix + "BASE_URL", "").strip().rstrip("/")
        if not model or not base_url:
            raise ModelConfigurationError(
                f"请在本机环境配置 {prefix}MODEL 和 {prefix}BASE_URL（{role} 模型）。"
            )
        return cls.validated(model, base_url, os.environ.get(prefix + "API_KEY", ""), prefix)

    @staticmethod
    def normalize_url(base_url: str, label: str) -> str:
        base_url = base_url.strip().rstrip("/")
        try:
            base_url = validate_base_url(base_url)
        except ValueError as exc:
            raise ModelConfigurationError(f"{label}BASE_URL：{exc}") from exc
        return base_url if base_url.endswith("/v1") else base_url + "/v1"

    @classmethod
    def validated(cls, model: str, base_url: str, api_key: str, label: str) -> ModelConfig:
        if not model.strip() or not base_url.strip():
            raise ModelConfigurationError(
                f"{label} 缺少模型名或服务地址，请配置默认连接或 Agent 覆盖。"
            )
        return cls(model.strip(), cls.normalize_url(base_url, label), api_key)

    def public_identity(self) -> JsonObject:
        return {"model": self.model, "base_url": self.base_url}


def validate_base_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and parsed.port != 0
            and not any((parsed.username, parsed.password, parsed.query, parsed.fragment))
            and not any(char.isspace() for char in value)
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("API 地址须为不含密钥、查询参数或空格的 HTTP(S) 地址")
    return value.rstrip("/")


@dataclass(frozen=True)
class ResearchConnection:
    base_url: str
    model: str
    reasoning_effort: str
    max_output_tokens: int = 16384
    context_window_tokens: int | None = None
    backend: str = "standard"

    def __post_init__(self) -> None:
        validate_base_url(self.base_url)
        if not self.model.strip() or not self.reasoning_effort.strip():
            raise ValueError("model and reasoning_effort must be explicit nonempty values")
        if type(self.max_output_tokens) is not int or self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be a positive integer")
        window = self.context_window_tokens
        if window is not None and (type(window) is not int or window <= self.max_output_tokens):
            raise ValueError("context window must reserve output space")
        if (
            self.backend == "standard"
            and self.effective_context_window_tokens <= self.max_output_tokens
        ):
            raise ValueError("context window must reserve output space")
        if self.backend == "codex" and self.max_output_tokens != 16384:
            raise ValueError(
                "max_output_tokens is only configurable for standard; "
                "Codex uses its native output limit"
            )

    @property
    def effective_context_window_tokens(self) -> int:
        # Only standard uses this fallback. Codex keeps its native model default.
        return self.context_window_tokens or (256_000 if self.model == "gpt-6-astra" else 128_000)

    @property
    def runtime_base_url(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/responses"):
            return base.removesuffix("/responses")
        return base if base.endswith("/v1") else base + "/v1"
