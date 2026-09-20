"""从宿主环境读取模型连接配置，不提供部署地址或凭证默认值。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit

from areal_rsi.api.base import JsonObject


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
            parsed = urlsplit(base_url)
            valid = (
                parsed.scheme in {"http", "https"}
                and parsed.hostname
                and parsed.port != 0
                and not any((parsed.username, parsed.password, parsed.query, parsed.fragment))
            )
        except ValueError:
            valid = False
        if not valid:
            raise ModelConfigurationError(
                f"{label}BASE_URL 必须为 HTTP(S) 服务地址，不能包含凭证或查询参数"
            )
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
