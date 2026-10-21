"""Project-owned API configuration; the user's normal Codex home is never changed."""

from __future__ import annotations

import json
import os
import re
import shutil
import tomllib
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from dotenv import dotenv_values

from areal_rsi.api.control import decode_record, encode_record
from areal_rsi.api.research import DEFAULT_DATA_ROOT, AppConfig, SessionConfig

SESSION_ENV = {
    "model": "RSI_MODEL",
    "effort": "RSI_REASONING_EFFORT",
    "base_url": "RSI_BASE_URL",
    "api_key_env": "RSI_API_KEY_ENV",
    "codex_bin": "RSI_CODEX_BIN",
    "sandbox": "RSI_SANDBOX",
    "approval_policy": "RSI_APPROVAL_POLICY",
    "keep_awake": "RSI_KEEP_AWAKE",
    "use_experience": "RSI_USE_EXPERIENCE",
    "auto_learn": "RSI_AUTO_LEARN",
}


def load_config(
    workspace: Path | None = None,
    *,
    config_file: Path | None = None,
    env_file: Path | None = None,
    root: Path | None = None,
) -> AppConfig:
    """Load per-project settings without changing the daemon's process environment."""
    directory = (workspace or Path.cwd()).expanduser().resolve()
    user = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "areal-rsi"
    path = (config_file or directory / "rsi.toml").expanduser().resolve()
    payload = {}
    for source in (user / "rsi.toml", path):
        if not source.exists() and not (source == path and config_file is not None):
            continue
        with source.open("rb") as handle:
            layer = tomllib.load(handle)
        if not isinstance(layer.get("session", {}), dict):
            raise ValueError("Config [session] must be a TOML table")
        # Validate each layer before merging so misspelled keys never disappear.
        decode_record(
            AppConfig,
            {**layer, "session": {"workspace": str(directory), **layer.get("session", {})}},
        )
        session = layer.pop("session", {})
        if "env_files" in session:
            raise ValueError("env_files is managed by RSI; select secrets using --env-file")
        if "workspace" in session:
            session["workspace"] = str(source.parent / Path(session["workspace"]).expanduser())
        if "data_root" in layer:
            layer["data_root"] = str(source.parent / Path(layer["data_root"]).expanduser())
        payload.update(layer)
        payload.setdefault("session", {}).update(session)
    dotenv = (env_file or directory / ".env").expanduser().resolve()
    if env_file is not None and not (dotenv.is_file() or dotenv == Path(os.devnull)):
        raise ValueError(f"Environment file not found: {dotenv}")
    files = [str(p) for p in (user / ".env", dotenv) if p.is_file()]
    if env_file is not None:
        files = [str(dotenv)]  # Explicit /dev/null also disables user secrets.
    environment = _environment(files)
    session = payload.setdefault("session", {})
    session.setdefault("workspace", str(directory))
    session["env_files"] = files
    for field, name in SESSION_ENV.items():
        if name in environment:
            value = environment[name]
            session[field] = (
                _boolean(name, value)
                if field in {"keep_awake", "use_experience", "auto_learn"}
                else value
            )
    config = decode_record(AppConfig, payload)
    if workspace is not None:
        config.session.workspace = str(directory)
    selected_root = root if root is not None else environment.get("RSI_DATA_ROOT")
    config.data_root = str(data_root(Path(selected_root or config.data_root)))
    config.session = validate_config(config.session)
    return config


def _environment(files: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for filename in files:
        # No interpolation through global environment or another project's .env.
        values.update(
            {k: v for k, v in dotenv_values(filename, interpolate=False).items() if v is not None}
        )
    return {**values, **os.environ}


def runtime_environment(config: SessionConfig) -> dict[str, str]:
    """Resolve credentials at process launch; only paths enter persisted records."""
    files = config.env_files
    if not files:
        user = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "areal-rsi"
        files = [str(user / ".env"), str(Path(config.workspace) / ".env")]
    return _environment(files)


def _boolean(name: str, value: str) -> bool:
    normalized = value.strip().lower()
    if normalized not in {"1", "0", "true", "false"}:
        raise ValueError(f"{name} must be 1/0 or true/false")
    return normalized in {"1", "true"}


def validate_config(config: SessionConfig) -> SessionConfig:
    config = decode_record(SessionConfig, encode_record(config))
    workspace = Path(config.workspace).expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError(f"Workspace is not a directory: {workspace}")
    config.workspace = str(workspace)
    config.model = config.model.strip()
    if not config.model:
        raise ValueError("model must not be empty")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", config.api_key_env):
        raise ValueError("api_key_env must name an environment variable")
    url = urlsplit(config.base_url)
    if (
        url.scheme not in {"http", "https"}
        or not url.hostname
        or any((url.username, url.password, url.query, url.fragment))
    ):
        raise ValueError("base_url must be an HTTP(S) URL without credentials or query")
    config.base_url = config.base_url.rstrip("/")
    if not url.path.rstrip("/"):
        config.base_url += "/v1"
    return config


def available_models(config: SessionConfig) -> list[str]:
    """Read this provider's catalog, independently of Codex's built-in model presets."""
    key = runtime_environment(config).get(config.api_key_env)
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
        (root if root is not None else Path(os.environ.get("RSI_DATA_ROOT", DEFAULT_DATA_ROOT)))
        .expanduser()
        .resolve()
    )


def executable(config: SessionConfig) -> str:
    binary = shutil.which(config.codex_bin)
    if binary is None:
        raise ValueError("Codex CLI not found. Install Codex >= 0.162.1 or set RSI_CODEX_BIN.")
    if not runtime_environment(config).get(config.api_key_env):
        raise ValueError(f"Set {config.api_key_env} in the project .env before starting Codex.")
    return binary
