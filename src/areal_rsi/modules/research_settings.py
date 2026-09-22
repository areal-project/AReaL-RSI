"""Connection settings consumed by the package-backed research runtime.

Native harnesses own their retry policy and compaction. Only settings that RSI
actually passes to those harnesses belong here.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


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
