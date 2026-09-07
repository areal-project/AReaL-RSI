"""从宿主环境读取模型连接配置，不提供部署地址或凭证默认值。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ModelConfig:
    model: str
    base_url: str
    api_key: str = field(repr=False)

    @classmethod
    def from_environment(cls) -> ModelConfig:
        model = os.environ.get("RSI_MODEL", "").strip()
        base_url = os.environ.get("RSI_BASE_URL", "").strip().rstrip("/")
        if not model or not base_url:
            raise ValueError("请在本机环境配置 RSI_MODEL 和 RSI_BASE_URL 后启动实验入口")
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
            raise ValueError("RSI_BASE_URL 必须为 HTTP(S) 服务地址，不能包含凭证或查询参数")
        if not base_url.endswith("/v1"):
            base_url += "/v1"
        return cls(model, base_url, os.environ.get("RSI_API_KEY", ""))
