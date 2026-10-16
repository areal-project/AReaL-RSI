"""从用户 JSON 文件与宿主环境读取模型连接配置，不提供部署地址或凭证默认值。"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from areal_rsi.api.base import JsonObject


class ModelConfigurationError(ValueError):
    """模型连接缺失或无效，可以直接向用户展示的配置错误。"""


def config_path() -> Path:
    return Path.home() / ".areal_rsi" / "config.json"


def init_config() -> Path:
    """创建仅当前用户可读写的模板；不覆盖已有配置。"""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    template = {
        "RSI_TEACHER_MODEL": "",
        "RSI_TEACHER_BASE_URL": "",
        "RSI_TEACHER_API_KEY": "",
        "RSI_TARGET_MODEL": "",
        "RSI_TARGET_BASE_URL": "",
        "RSI_TARGET_API_KEY": "",
        "RSI_DATA_ROOT": ".rsi/local",
        "RSI_BENCHMARK": "arithmetic-v1",
        "RSI_RESEARCH_AGENT_COUNT": 1,
        "RSI_AGENT_MODELS": {},
        "RSI_RUNTIME_CONFIG": "",
        "RSI_TUI_MODEL_API": "chat-completions",
        "RSI_TURN_TIMEOUT": 1200,
    }
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path
    with os.fdopen(fd, "w") as stream:
        stream.write(json.dumps(template, ensure_ascii=False, indent=2) + "\n")
    return path


def load_user_config() -> None:
    """在 CLI 启动时加载默认配置；显式环境变量（包括空值）优先。"""
    path = config_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ModelConfigurationError(f"无法读取 {path}，请检查权限及 JSON 格式。") from None
    if not isinstance(raw, dict):
        raise ModelConfigurationError(f"{path} 必须是 JSON 对象。")
    values: dict[str, str] = {}
    for key, value in raw.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ModelConfigurationError("config.json 字段名必须为合法环境变量名。")
        if key in {"RSI_AGENT_MODELS", "RSI_RESEARCH_SKILLS"} and isinstance(value, dict):
            values[key] = json.dumps(value, ensure_ascii=False)
        elif isinstance(value, str):
            values[key] = value
        elif type(value) in (int, float):
            values[key] = str(value)
        else:
            raise ModelConfigurationError(
                "config.json 配置值必须为字符串或数字；"
                "RSI_AGENT_MODELS、RSI_RESEARCH_SKILLS 可使用 JSON 对象。"
            )
    # 完整验证后再更新，避免无效文件造成部分配置生效。
    for key, value in values.items():
        os.environ.setdefault(key, value)


def research_agent_count_from_environment() -> int:
    raw = os.environ.get("RSI_RESEARCH_AGENT_COUNT", "1").strip()
    if not re.fullmatch(r"[0-9]+", raw):
        raise ModelConfigurationError("RSI_RESEARCH_AGENT_COUNT 必须为 1 到 8 的整数。")
    count = int(raw)
    if not 1 <= count <= 8:
        raise ModelConfigurationError("RSI_RESEARCH_AGENT_COUNT 必须为 1 到 8 的整数。")
    return count


@dataclass(frozen=True)
class ModelConfig:
    model: str
    base_url: str
    api_key: str = field(repr=False)

    @staticmethod
    def environment_prefix(role: Literal["teacher", "target"]) -> str:
        prefix = f"RSI_{role.upper()}_"
        names = ("MODEL", "BASE_URL", "API_KEY")
        if (
            role == "teacher"
            and not any(prefix + name in os.environ for name in names)
            and any("RSI_" + name in os.environ for name in names)
        ):
            return "RSI_"
        return prefix

    @classmethod
    def from_environment(cls, role: Literal["teacher", "target"] = "teacher") -> ModelConfig:
        prefix = cls.environment_prefix(role)
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


class AgentModelSettings:
    """解析 Interaction Agent、RSI Agent 和 ResearchAgent；Task Agent 直接使用 target。"""

    def __init__(self, overrides: dict[str, dict[str, str]]):
        self.overrides = overrides
        self.credential_env_names = frozenset(
            entry["api_key_env"] for entry in overrides.values() if "api_key_env" in entry
        )

    @classmethod
    def from_environment(cls) -> AgentModelSettings:
        try:
            raw = json.loads(os.environ.get("RSI_AGENT_MODELS", "{}"))
        except json.JSONDecodeError:
            raise ModelConfigurationError("RSI_AGENT_MODELS 必须为合法 JSON 对象。") from None
        if not isinstance(raw, dict):
            raise ModelConfigurationError("RSI_AGENT_MODELS 必须按 agent_id 映射模型配置。")
        allowed = {"model", "base_url", "api_key_env"}
        for agent_id, entry in raw.items():
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", agent_id):
                raise ModelConfigurationError("RSI_AGENT_MODELS 中的 agent_id 格式不合法。")
            if not isinstance(entry, dict) or set(entry) - allowed:
                raise ModelConfigurationError(
                    "Agent 覆盖仅接受 model、base_url、api_key_env；密钥值请通过环境变量提供。"
                )
            if any(not isinstance(value, str) or not value.strip() for value in entry.values()):
                raise ModelConfigurationError("Agent 模型覆盖值必须为非空字符串。")
            if "api_key_env" in entry and not re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]*", entry["api_key_env"]
            ):
                raise ModelConfigurationError("api_key_env 必须为环境变量名。")
        return cls(raw)

    def validate_agents(self) -> None:
        supported = {"interaction-agent", "rsi-agent", "research-agent"}
        if set(self.overrides) - supported:
            raise ModelConfigurationError(
                "RSI_AGENT_MODELS 只支持 interaction-agent、rsi-agent 和 research-agent；"
                "Task Agent 直接使用 RSI_TARGET 配置。"
            )

    def resolve(self, agent_id: str) -> ModelConfig:
        if agent_id == "task-agent":
            raise ModelConfigurationError("Task Agent 直接使用 RSI_TARGET 配置，不单独配置模型。")
        prefix = ModelConfig.environment_prefix("teacher")
        model = os.environ.get(prefix + "MODEL", "")
        base_url = os.environ.get(prefix + "BASE_URL", "")
        api_key = os.environ.get(prefix + "API_KEY", "")
        identifier = "research-agent" if agent_id.startswith("research-agent-") else agent_id
        override = self.overrides.get(identifier, {})
        if "base_url" in override:
            new_url = ModelConfig.normalize_url(override["base_url"], identifier + " ")
            try:
                previous_url = ModelConfig.normalize_url(base_url, prefix)
            except ModelConfigurationError:
                previous_url = None
            if new_url != previous_url:
                api_key = ""
            base_url = new_url
        model = override.get("model", model)
        if "api_key_env" in override:
            name = override["api_key_env"]
            if name not in os.environ:
                raise ModelConfigurationError(f"{identifier} 的 api_key_env 引用变量未设置。")
            api_key = os.environ[name]
        if not model.strip() or not base_url.strip():
            raise ModelConfigurationError(
                f"请配置 {prefix}MODEL 和 {prefix}BASE_URL，或为 {agent_id} 提供完整模型覆盖。"
            )
        return ModelConfig.validated(model, base_url, api_key, agent_id + " ")

    def interaction_name(self) -> str | None:
        try:
            return self.resolve("interaction-agent").model
        except ModelConfigurationError:
            # Interaction Agent 不是后台 Run 的执行依赖；实际启动 TUI 时仍会严格调用 resolve。
            return None
