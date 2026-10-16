"""Project-owned API configuration; the user's normal Codex home is never changed."""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from areal_rsi.api.control import decode_record, encode_record
from areal_rsi.api.research import SessionConfig


def from_environment(workspace: Path | None = None) -> SessionConfig:
    return validate_config(
        decode_record(
            SessionConfig,
            {
                "workspace": str((workspace or Path.cwd()).resolve()),
                "model": os.environ.get("RSI_MODEL", "gpt-6-astra"),
                "effort": os.environ.get("RSI_REASONING_EFFORT", "high"),
                "base_url": os.environ.get("RSI_BASE_URL", "https://api.openai.com/v1"),
                "api_key_env": os.environ.get("RSI_API_KEY_ENV", "RSI_API_KEY"),
                "codex_bin": os.environ.get("RSI_CODEX_BIN", "codex"),
                "sandbox": os.environ.get("RSI_SANDBOX", "danger-full-access"),
                "approval_policy": os.environ.get("RSI_APPROVAL_POLICY", "never"),
                "keep_awake": os.environ.get("RSI_KEEP_AWAKE", "1") == "1",
                "use_experience": os.environ.get("RSI_USE_EXPERIENCE", "1") == "1",
                "auto_learn": os.environ.get("RSI_AUTO_LEARN", "1") == "1",
            },
        )
    )


def validate_config(config: SessionConfig) -> SessionConfig:
    config = decode_record(SessionConfig, encode_record(config))
    workspace = Path(config.workspace).expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError(f"Workspace is not a directory: {workspace}")
    config.workspace = str(workspace)
    if not config.model.strip():
        raise ValueError("RSI_MODEL must not be empty")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", config.api_key_env):
        raise ValueError("api_key_env must name an environment variable")
    url = urlsplit(config.base_url)
    if (
        url.scheme not in {"http", "https"}
        or not url.hostname
        or any((url.username, url.password, url.query, url.fragment))
    ):
        raise ValueError("RSI_BASE_URL must be an HTTP(S) URL without credentials or query")
    config.base_url = config.base_url.rstrip("/")
    if not url.path.rstrip("/"):
        config.base_url += "/v1"
    return config


def available_models(config: SessionConfig) -> list[str]:
    """Read this provider's catalog, independently of Codex's built-in model presets."""
    key = os.environ.get(config.api_key_env)
    if not key:
        raise ValueError(f"未设置 {config.api_key_env}，无法查询模型列表")
    request = Request(
        config.base_url.rstrip("/") + "/models", headers={"Accept": "application/json"}
    )
    # A redirected request must not forward the provider's credential.
    request.add_unredirected_header("Authorization", "Bearer " + key)
    try:
        with urlopen(request, timeout=10) as response:
            payload = json.load(response)
    except HTTPError as exc:
        raise ValueError(f"模型列表查询失败（HTTP {exc.code}）") from None
    except (URLError, OSError) as exc:
        raise ValueError("无法连接模型列表接口，请稍后重试") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("模型列表格式无效：需要包含 data 数组")
    models = decode_record(
        list[str],
        [item.get("id") if isinstance(item, dict) else None for item in payload["data"]],
    )
    if any(not model.strip() for model in models):
        raise ValueError("模型列表包含空模型名")
    return sorted(set(models))


def data_root(root: Path | None = None) -> Path:
    return (
        (root or Path(os.environ.get("RSI_DATA_ROOT", str(Path.home() / ".local/share/areal-rsi"))))
        .expanduser()
        .resolve()
    )


def executable(config: SessionConfig) -> str:
    binary = shutil.which(config.codex_bin)
    if binary is None:
        raise ValueError("Codex CLI not found. Install Codex >= 0.162.1 or set RSI_CODEX_BIN.")
    if not os.environ.get(config.api_key_env):
        raise ValueError(f"Set {config.api_key_env} in the project .env before starting Codex.")
    return binary
