"""从包内模板生成一次 DSH TUI 启动所需的动态 Cordis patch。"""

from __future__ import annotations

import json
import os
import sys
from importlib.resources import files
from pathlib import Path
from typing import Any, cast

import yaml  # type: ignore[import-untyped]

from areal_rsi.config import AgentModelSettings
from areal_rsi.modules.remote.registry import all_remote_environment_names

PERSONA = files("areal_rsi").joinpath("resources/dsh/persona.md").read_text()

MCP_ENVIRONMENT = (
    "RSI_MODEL",
    "RSI_BASE_URL",
    "RSI_API_KEY",
    "RSI_REASONING_EFFORT",
    "RSI_TEACHER_MODEL",
    "RSI_TEACHER_BASE_URL",
    "RSI_TEACHER_API_KEY",
    "RSI_TARGET_MODEL",
    "RSI_TARGET_BASE_URL",
    "RSI_TARGET_API_KEY",
    "RSI_AGENT_MODELS",
    "RSI_RUNTIME_CONFIG",
    "ARENA_PROFILE",
    "ARENA_BASE_URL",
    "ARENA_OPENAPI_TOKEN",
    "ENVARENA_API_TOKEN",
)


class _EnvironmentExpression(str):
    """DSH 在加载配置时求值的表达式，文件内只保存变量名。"""


yaml.SafeDumper.add_representer(
    _EnvironmentExpression,
    lambda dumper, value: dumper.represent_scalar("tag:yaml.org,2002:js", str(value)),
)


def _template() -> list[dict[str, Any]]:
    value = yaml.safe_load(files("areal_rsi").joinpath("resources/dsh/entry.patch.yml").read_text())
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise RuntimeError("DSH entry patch 模板必须为对象列表")
    return cast(list[dict[str, Any]], value)


def _entry(tree: list[dict[str, Any]], identifier: str) -> dict[str, Any]:
    for row in tree:
        if row.get("id") == identifier:
            return row
        for inserted in row.get("insert", []):
            if isinstance(inserted, dict) and inserted.get("id") == identifier:
                return inserted
    raise RuntimeError(f"DSH entry patch 模板缺少 {identifier}")


def write_profile_patch(
    root: Path,
    workspace: Path,
    model: str,
    *,
    port: int = 8197,
    run_id: str | None = None,
    destination: Path | None = None,
) -> Path:
    """填充非敏感动态值；凭证只通过运行时环境变量传给 MCP。"""
    root, workspace = root.resolve(), workspace.resolve()
    tree = _template()
    agent = _entry(tree, "dsh-tui")["config"]
    agent.update({"model": model, "cwd": str(workspace)})
    _entry(tree, "system-prompt")["config"]["persona"] = PERSONA
    adapter = _entry(tree, "llm-deepseek")["config"]
    adapter.update(
        {
            "defaultContextWindow": int(os.environ.get("RSI_CONTEXT_WINDOW", "32768")),
            "models": [{"id": model}],
        }
    )
    api = os.environ.get("RSI_TUI_MODEL_API", "chat-completions")
    if api not in {"chat-completions", "responses"}:
        raise ValueError("RSI_TUI_MODEL_API must be chat-completions or responses")
    if api == "responses":
        agent["provider"] = "rsi-responses"
        tree.append(
            {
                "id": "llm-pi-ai",
                "config": {
                    "providers": {
                        "rsi-responses": {
                            "api": "openai-responses",
                            "apiKeyEnv": "DEEPSEEK_API_KEY",
                            "baseURL": _EnvironmentExpression(
                                "process.env.DEEPSEEK_BASE_URL + '/v1'"
                            ),
                            "defaultContextWindow": int(
                                os.environ.get("RSI_CONTEXT_WINDOW", "32768")
                            ),
                            "defaultMaxTokens": 8192,
                            "retryPolicy": {"mode": "normal", "maxRetries": 0},
                            "models": [{"id": model}],
                        }
                    }
                },
            }
        )
    environment_names = [
        *MCP_ENVIRONMENT,
        *all_remote_environment_names(),
        *sorted(AgentModelSettings.from_environment().credential_env_names),
    ]
    mcp = _entry(tree, "rsi-mcp")["config"]
    mcp.update(
        {
            "command": sys.executable,
            "args": ["-m", "areal_rsi.ui.mcp", "--port", str(port), "--root", str(root)]
            + (["--run", run_id] if run_id else []),
            "cwd": str(workspace),
            "env": _EnvironmentExpression(
                "Object.fromEntries("
                + json.dumps(environment_names)
                + ".flatMap(key => process.env[key] === undefined "
                "? [] : [[key, process.env[key]]]))"
            ),
        }
    )
    patch = destination or root / "dsh-entry.patch.yml"
    patch.parent.mkdir(parents=True, exist_ok=True)
    patch.write_text(yaml.safe_dump(tree, allow_unicode=True, sort_keys=False))
    return patch
