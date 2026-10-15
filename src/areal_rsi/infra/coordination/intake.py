"""Optional pre-run conversation and operator execution validation."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from areal_rsi.api.interfaces import ToolCall, ToolHandler, ToolResult
from areal_rsi.api.research import TaskBrief
from areal_rsi.infra.coordination.config import (
    ResearchConfig,
    git_head_commit,
)
from areal_rsi.infra.coordination.evaluation.verification import IDENTITY_FIELDS
from areal_rsi.infra.coordination.tools.actions import ResearchActions, definition


def reject_credentials(value: Any) -> None:
    """Configuration stores environment names, not credential values."""
    if isinstance(value, dict):
        for name, item in value.items():
            upper = name.upper()
            if item and (
                upper in {"API_KEY", "PASSWORD", "SECRET", "TOKEN", "AUTHORIZATION", "PRIVATE_KEY"}
                or upper.endswith(("_API_KEY", "_PASSWORD", "_SECRET", "_ACCESS_TOKEN"))
            ):
                raise ValueError("请将密钥保存在进程环境变量中，不要写入研究配置")
            reject_credentials(item)
    elif isinstance(value, list):
        for item in value:
            reject_credentials(item)
    elif isinstance(value, str) and (
        re.search(r"sk-[A-Za-z0-9_-]{24,}", value)
        or re.search(
            r"(?im)^\s*(?:export\s+)?[\w-]*(?:api[_-]?key|password|secret|access_token)"
            r"\s*[:=]\s*\S+",
            value,
        )
    ):
        raise ValueError("配置中不能包含密钥，请使用环境变量")


def validate_execution(config: ResearchConfig) -> list[str]:
    """Validate declared resources and fixed commands without running candidates."""
    issues: list[str] = []
    if not isinstance(config.execution_env, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in config.execution_env.items()
    ):
        return ["环境变量名称和值必须是字符串"]
    reject_credentials(config.public())
    if config.metadata.get("simulation_only"):
        issues.append("当前配置不是正式执行配置")
    if config.seed_dir and not Path(config.seed_dir).is_dir():
        issues.append("起点目录不存在：" + config.seed_dir)
    if config.workspace_dir:
        try:
            git_head_commit(config.workspace_dir)
        except ValueError as exc:
            issues.append(str(exc))
    commands = [*config.commands, *([config.verifier] if config.verifier else [])]
    if len({cmd.name for cmd in commands}) != len(commands):
        issues.append("执行命令名称不能重复")
    for cmd in commands:
        if not cmd.command or not cmd.command[0] or "{" in cmd.command[0]:
            issues.append(f"{cmd.name} 缺少固定的执行程序")
            continue
        executable = Path(cmd.command[0])
        if not (executable.is_file() and os.access(executable, os.X_OK)) and not shutil.which(
            cmd.command[0]
        ):
            issues.append(f"找不到 {cmd.name} 的执行程序")
        if type(cmd.gpu_count) is not int or cmd.gpu_count < 0:
            issues.append(f"{cmd.name} 的 GPU 数量无效")
        elif cmd.gpu_count > len(config.brief.limits.gpu_ids):
            issues.append(f"{cmd.name} 需要 {cmd.gpu_count} 张 GPU，但尚未配置足够设备")
        if cmd.timeout_seconds is not None and (
            isinstance(cmd.timeout_seconds, bool)
            or not isinstance(cmd.timeout_seconds, (int, float))
            or not math.isfinite(cmd.timeout_seconds)
            or cmd.timeout_seconds <= 0
        ):
            issues.append(f"{cmd.name} 的超时必须大于零")
        if Path(cmd.output_subdir).is_absolute() or ".." in Path(cmd.output_subdir).parts:
            issues.append(f"{cmd.name} 的公开输出目录必须是作业内的相对路径")
        if cmd.auto_verify_output is not None and (
            not cmd.auto_verify_output
            or Path(cmd.auto_verify_output).is_absolute()
            or ".." in Path(cmd.auto_verify_output).parts
        ):
            issues.append(f"{cmd.name} 的产物目录必须是作业内的相对路径")
        for arg in cmd.command:
            placeholder = re.fullmatch(r"\{([A-Za-z_]\w*)\}", arg)
            if placeholder and placeholder[1] not in cmd.parameters:
                issues.append(f"{cmd.name} 的占位符 {arg} 未在 parameters 中声明")
            for parameter in cmd.parameters:
                marker = "{" + parameter + "}"
                if marker in arg and arg != marker:
                    issues.append(f"{cmd.name} 的参数 {marker} 必须独占一个 argv 元素")
    if config.verifier and not config.metadata.get("verification_identity"):
        issues.append("验证程序尚未提供固定的协议和数据身份")
    # Existing experiment commands may produce scores for researchers to inspect.
    # A trusted verifier remains mandatory for target-based stopping.
    if (
        config.brief.verification is not None
        and config.verifier is None
        and (config.brief.verification.target is not None or not config.commands)
    ):
        issues.append("已约定验证目标或缺少实验命令，但尚未接入实际验证程序")
    if config.brief.verification is not None and config.brief.limits.max_verify_calls == 0:
        issues.append("已约定验证指标，但验证调用预算为零")
    if config.verifier and config.brief.verification is None:
        issues.append("已接入验证器，还需要约定验证指标和比较方式")
    identity = config.metadata.get("verification_identity")
    if config.verifier and identity:
        fields = (
            IDENTITY_FIELDS.get(identity.get("adapter", "")) if isinstance(identity, dict) else None
        )
        if fields is None:
            issues.append("验证器须使用 content 通用结果协议或已支持的适配器")
        else:
            phases = identity.get("phases", {})
            required = {"search"}
            if config.brief.verification and config.brief.verification.identity.get(
                "require_final_confirmation"
            ):
                required.add("final")
            for phase in required:
                values = phases.get(phase) if isinstance(phases, dict) else None
                if not isinstance(values, dict) or any(not values.get(field) for field in fields):
                    issues.append(f"{phase} 的验证协议与数据身份尚未完整固定")
    if config.metadata.get("template") == "kernelbench":
        environment = {**os.environ, **config.execution_env}
        for name in ("RSI_TARGET_MODEL", "RSI_TARGET_BASE_URL", "RSI_TARGET_API_KEY"):
            if not environment.get(name):
                issues.append("尚未配置目标模型连接：" + name)
        url = urlsplit(environment.get("RSI_TARGET_BASE_URL", ""))
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or any((url.username, url.password, url.query, url.fragment))
        ):
            issues.append("目标模型接口须为不含密钥或查询参数的 HTTP(S) 地址")
    return issues


def missing_requirements(brief: TaskBrief) -> list[str]:
    missing = []
    if brief.objective in {"新的研究", "新研究"} or not brief.instructions.strip():
        missing.append("任务描述")
    if not brief.acceptance:
        missing.append("验收标准")
    return missing


class ResearchIntake:
    """Optional conversation before execution; callers may instead supply a full config."""

    def __init__(self, actions: ResearchActions, start: Callable[[], Awaitable[None]]):
        self.actions, self.c, self.start = actions, actions.c, start
        for tool in (
            definition("get_setup", "查看已配置的工作区、命令和待澄清的需求。"),
            definition("start_research", "需求足够明确后交给 Leader 执行；不代替用户定义新目标。"),
        ):
            actions.register(tool, self.handler, roles={"interaction"})

    def handler(self, agent_id: str) -> ToolHandler:
        async def handle(call: ToolCall) -> ToolResult:
            if self.c.state.status != "planned":
                raise ValueError("Intake ends when execution starts")
            path = self.c.store.root / "coordination/config.json"
            from areal_rsi.infra.coordination.config import config_from_dict

            config = config_from_dict(json.loads(path.read_text()))
            if call.name == "get_setup":
                return ToolResult(
                    {
                        "mode": config.mode,
                        "workspace": config.workspace_dir or config.seed_dir,
                        "commands": [cmd.name for cmd in config.commands],
                        "verifier": config.verifier.name if config.verifier else None,
                        "missing": missing_requirements(self.c.store.brief()),
                    }
                )
            await self.start()
            return ToolResult({"started": True}, stop=True, reason="handoff")

        return handle
