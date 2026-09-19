"""Conversation intake and RSI-owned execution setup before configuration freezes."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from areal_rsi.api.research import ResearchMessage
from areal_rsi.base.storage import decode, encode, now, write_json
from areal_rsi.coordination.actions import STRING, ResearchActions, definition
from areal_rsi.coordination.config import ResearchConfig, load_config
from areal_rsi.coordination.coordinator import effect_id
from areal_rsi.coordination.verification import IDENTITY_FIELDS
from areal_rsi.execution import ApprovedCommand
from areal_rsi.modules.responses import ToolCall, ToolHandler, ToolResult


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


class ResearchIntake:
    def __init__(
        self,
        actions: ResearchActions,
        *,
        repository: Path,
        start: Callable[[str | None], Awaitable[None]],
        freeze_execution: Callable[[], Awaitable[None]] | None = None,
    ):
        self.actions, self.c, self.repository, self.start = actions, actions.c, repository, start
        self.freeze_execution = freeze_execution
        actions.register(
            definition(
                "handoff_research",
                "用户已经明确要求开始且需求已谈清时，直接将需求交给 RSI 并启动研究。"
                "summary 简述目标、约束、评判方式和已知环境；confirmation_message_id 必须来自"
                "当前真实用户消息。执行封装尚未完成时由 RSI 组织接入，"
                "冻结验证配置前不启动正式作业。",
                summary=STRING,
                confirmation_message_id=STRING,
                execution_required={"type": "boolean"},
            ),
            self.handler,
            roles={"interaction"},
        )
        actions.register(
            definition(
                "freeze_execution", "RSI 在接入完成后检查并冻结执行配置，随后允许正式作业。"
            ),
            self.handler,
            roles={"rsi"},
        )
        for tool in (
            definition("get_setup", "查看需求整理进度、执行配置和仍缺少的条件。"),
            definition(
                "inspect_environment",
                "查看用户提供的本机目录或配置文件；不执行其中的代码，不读取密钥文件。",
                path=STRING,
            ),
            definition(
                "set_execution",
                "按对话约定保存起点目录、普通执行命令和非敏感环境变量。"
                "JSON 可含 seed_dir、commands、execution_env、description；"
                "命令格式为 name/command(argv数组)/parameters/"
                "gpu_count/timeout_seconds/output_subdir。"
                "系统只自动绑定 {workspace} 和 {output}，须同时声明在 parameters 中；"
                "例如 command=['/abs/python','experiment.py','{output}'], parameters=['output']。"
                "output_subdir 是作业已有的公开输出子目录，通常用 '.'，不凭空设定子目录。"
                "验证器通过 load_execution_config 或 prepare_adapter 接入。",
                execution_json=STRING,
            ),
            definition(
                "load_execution_config",
                "导入用户提供的正式 ResearchConfig 文件；"
                "保留当前研究需求，接入其中的执行与验证配置。",
                path=STRING,
            ),
            definition(
                "get_adapter_guide",
                "查看可选内置适配器的环境字段和接入说明。adapter 为 kernelbench。"
                "这些是参考格式，不应直接把占位路径交给用户填写。",
                adapter=STRING,
            ),
            definition(
                "prepare_adapter",
                "可选的现成执行适配器：根据已讨论的环境准备 KernelBench 的正式配置。"
                "只检查环境和固定协议，不调用目标模型或执行候选。"
                "settings_json 为操作员环境 JSON；已有 prepared_data 可直接提供，"
                "KernelBench 也可提供 repository 和 dataset_config 来准备用户指定的题池。"
                "target_model/target_base_url 仅用于 KernelBench，其他任务传空字符串。",
                adapter=STRING,
                settings_json=STRING,
                target_model=STRING,
                target_base_url=STRING,
            ),
            definition(
                "prepare_research",
                "需求谈清后保存给用户确认的简明总结，检查执行条件，然后等待用户。"
                "execution_required 表示任务是否需要实际运行代码或实验；纯资料研究可以为 false。"
                "不强制要求 baseline、产物或数值 target。",
                summary=STRING,
                execution_required={"type": "boolean"},
            ),
            definition(
                "start_research",
                "用户在需求总结后明确要求开始时启动研究。confirmation_message_id "
                "必须是 get_state 返回的当前用户消息 ID；不能把下级报告或自己的决定当作用户确认。",
                confirmation_message_id=STRING,
            ),
        ):
            roles = {"interaction"}
            if tool["name"] not in {"prepare_research", "start_research"}:
                roles.add("rsi")
            actions.register(tool, self.handler, roles=roles)

    @property
    def path(self) -> Path:
        return self.c.store.root / "coordination" / "config.json"

    def config(self) -> ResearchConfig:
        config: ResearchConfig = decode(ResearchConfig, json.loads(self.path.read_text()))
        config.brief = self.c.store.brief()
        return config

    def save(self, config: ResearchConfig, description: str) -> None:
        config.__post_init__()
        value = config.public()
        reject_credentials(value)
        digest = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
        write_json(self.path.parent / "configurations" / f"{digest}.json", value, immutable=True)
        write_json(self.path, value)
        intake = self.c.state.setdefault("intake", {})
        intake.update(environment=description, config_revision=digest)
        if self.c.state["status"] == "planned":
            intake.update(status="discussing", prepared_at=None)
        self.c.save()
        self.c.store.event("environment_updated", description=description)

    def setup(self) -> dict[str, Any]:
        config = self.config()
        return {
            **self.c.state.get("intake", {}),
            "execution_setup": self.c.state.get("execution_setup", "ready"),
            "seed_dir": config.seed_dir,
            "commands": encode(config.commands),
            "verifier_available": config.verifier is not None,
            "experiment_commands_available": bool(config.commands),
            "result_evaluation": (
                "trusted_verifier"
                if config.verifier
                else "researcher_reads_command_results"
                if config.commands
                else "unconfigured"
            ),
            "execution_env": config.execution_env,
            "issues": validate_execution(config),
            "target_key_configured": bool(os.environ.get("RSI_TARGET_API_KEY")),
            "research_model": config.model_settings()["model"],
            "research_reasoning_effort": config.model_settings()["reasoning_effort"],
            "collaboration": {
                "mode": config.mode,
                "modules": list(config.collaboration_modules),
                "operator_selected": True,
            },
        }

    def import_config(self, config: ResearchConfig, description: str) -> None:
        current = self.config()
        incoming = config.brief
        if current.brief.limits.gpu_ids and current.brief.limits.gpu_ids != incoming.limits.gpu_ids:
            raise ValueError("导入配置的 GPU 与已约定资源不同，请先与用户确认")
        allowed = current.brief.limits.allowed_paths
        if allowed != ("*",) and incoming.limits.allowed_paths not in {allowed, ("*",)}:
            raise ValueError("导入配置的可修改路径与当前要求不同，请先确认修改范围")
        if (
            current.brief.verification
            and incoming.verification
            and (
                current.brief.verification.metric != incoming.verification.metric
                or current.brief.verification.direction != incoming.verification.direction
            )
        ):
            raise ValueError("导入配置的验证指标与当前需求不同，请先与用户确认")
        config.brief = current.brief
        if self.c.state["status"] != "planned":
            if incoming.limits.gpu_ids != current.brief.limits.gpu_ids:
                raise ValueError("接入不能改变已约定 GPU 资源")
            if incoming.limits.allowed_paths != current.brief.limits.allowed_paths:
                raise ValueError("接入不能改变已约定的修改范围")
            if incoming.verification and current.brief.verification:
                for key, value in current.brief.verification.identity.items():
                    if incoming.verification.identity.get(key) != value:
                        raise ValueError("接入不能改变已约定的验证条件：" + key)
        config.backend = current.backend
        config.model, config.reasoning_effort = current.model, current.reasoning_effort
        config.base_url, config.api_key_env = current.base_url, current.api_key_env
        config.credential_file = current.credential_file
        config.model_settings_pinned = current.model_settings_pinned
        config.model_settings_revision = current.model_settings_revision
        # Collaboration is an operator-selected ablation, not an environment
        # adapter's choice. Importing tools must preserve that selection.
        config.mode = current.mode
        config.collaboration_modules = current.collaboration_modules
        config.brief.limits.gpu_ids = incoming.limits.gpu_ids
        config.brief.limits.allowed_paths = (
            incoming.limits.allowed_paths if allowed == ("*",) else allowed
        )
        config.metadata.setdefault("execution_instructions", incoming.instructions)
        if config.brief.verification is None:
            config.brief.verification = incoming.verification
        elif incoming.verification:
            config.brief.verification.identity = incoming.verification.identity
        config.__post_init__()
        self.save(config, description)
        run = self.c.store.run()
        ref = self.c.store.put(
            "briefs",
            effect_id("environment", self.c.state["intake"]["config_revision"]),
            config.brief,
        )
        run.brief_ref = ref
        self.c.store.save_run(run)

    def handler(self, agent_id: str) -> ToolHandler:
        async def handle(call: ToolCall) -> ToolResult:
            if call.name == "get_setup":
                return ToolResult(self.setup())
            if call.name == "handoff_research":
                identifier = call.arguments["confirmation_message_id"]
                if (
                    self.c.state.get("started_at")
                    and self.c.state.get("intake", {}).get("confirmation_message_id") == identifier
                ):
                    return ToolResult({"started": True}, stop=True, reason="handoff")
                message = self.c.store.get("messages", identifier, ResearchMessage)
                if (
                    identifier not in self.c.current_message_ids(agent_id)
                    or message.sender != "user"
                ):
                    raise ValueError("启动依据必须是当前回合真实用户的明确指令")
                config = self.config()
                summary = call.arguments["summary"].strip()
                if (
                    not summary
                    or not config.brief.instructions.strip()
                    or config.brief.objective in {"新的研究", "新研究"}
                ):
                    raise ValueError("先保存明确的目标、约束和研究说明")
                if self.c.state["status"] != "planned":
                    raise ValueError("研究已经开始")
                self.c.state["execution_setup"] = "pending"
                self.c.state.setdefault("intake", {}).update(
                    status="ready",
                    summary=summary,
                    execution_required=call.arguments["execution_required"],
                )
                self.c.save()
                await self.start(identifier)
                self.c.enqueue(
                    "interaction",
                    "user",
                    summary + "\n\n研究已启动，RSI 已接手推进。",
                    key="handoff:" + call.call_id,
                )
                return ToolResult({"started": True}, stop=True, reason="handoff")
            if (
                call.name == "start_research"
                and self.c.state.get("started_at")
                and self.c.state.get("intake", {}).get("confirmation_message_id")
                == call.arguments["confirmation_message_id"]
            ):
                return ToolResult({"started": True})
            preparing = (
                agent_id == "rsi"
                and self.c.state["status"] == "running"
                and self.c.state.get("execution_setup") == "pending"
            )
            if call.name == "freeze_execution":
                if self.c.state.get("execution_setup") == "ready":
                    return ToolResult({"ready": True})
                if not preparing or self.freeze_execution is None:
                    raise ValueError("当前没有待完成的 RSI 执行接入")
                await self.freeze_execution()
                return ToolResult({"ready": True})
            if (
                self.c.state["status"] != "planned"
                and not preparing
                and call.name not in {"inspect_environment", "get_adapter_guide"}
            ):
                raise ValueError("研究已经开始，执行方式和评测条件不能再改；需要变更时新建研究")
            args = call.arguments
            if call.name == "get_adapter_guide":
                adapter = args["adapter"]
                if adapter not in {"kernelbench"}:
                    raise ValueError("内置适配器为 kernelbench；其他任务使用通用执行配置")
                folder = self.repository / "examples" / adapter
                return ToolResult(
                    {
                        "settings_example": json.loads(
                            (folder / "operator.example.json").read_text()
                        ),
                        "documentation": (folder / "README.md").read_text()[:20000],
                        "repository": str(self.repository),
                    }
                )
            if call.name == "inspect_environment":
                path = Path(args["path"]).expanduser()
                if not path.is_absolute():
                    raise ValueError("请使用用户提供的绝对路径")
                if path.is_dir():
                    return ToolResult(
                        {
                            "path": str(path),
                            "entries": sorted(
                                p.name for p in path.iterdir() if not p.name.startswith(".")
                            )[:100],
                        }
                    )
                if path.name.startswith("."):
                    raise ValueError("不读取隐藏配置或密钥文件，请提供环境说明")
                if path.suffix.lower() not in {
                    ".json",
                    ".md",
                    ".toml",
                    ".yaml",
                    ".yml",
                }:
                    return ToolResult(
                        {
                            "path": str(path),
                            "exists": path.is_file(),
                            "executable": path.is_file() and os.access(path, os.X_OK),
                        }
                    )
                if path.stat().st_size > 64_000:
                    raise ValueError("配置文件过大，请提供相关配置的路径")
                text = path.read_text()
                reject_credentials(json.loads(text) if path.suffix == ".json" else text)
                return ToolResult({"path": str(path), "content": text})
            if call.name == "set_execution":
                value = json.loads(args["execution_json"])
                if not isinstance(value, dict) or set(value) - {
                    "seed_dir",
                    "commands",
                    "execution_env",
                    "description",
                }:
                    raise ValueError(
                        "执行配置只接受 seed_dir、commands、execution_env 和 description"
                    )
                reject_credentials(value)
                config = self.config()
                if "seed_dir" in value:
                    config.seed_dir = value["seed_dir"] or None
                    if config.seed_dir and not Path(config.seed_dir).is_absolute():
                        raise ValueError("起点目录必须使用绝对路径")
                if "commands" in value:
                    config.commands = tuple(
                        decode(ApprovedCommand, item) for item in value["commands"]
                    )
                if "execution_env" in value:
                    config.execution_env = value["execution_env"]
                if "description" in value:
                    config.metadata["execution_instructions"] = value["description"]
                self.save(config, value.get("description", "已整理执行环境"))
                return ToolResult(self.setup())
            if call.name == "load_execution_config":
                path = Path(args["path"]).expanduser()
                if not path.is_absolute():
                    raise ValueError("执行配置必须使用绝对路径")
                reject_credentials(json.loads(path.read_text()))
                config = load_config(path)
                self.import_config(config, "已接入执行配置：" + str(path))
                return ToolResult(self.setup())
            if call.name == "prepare_adapter":
                from areal_rsi.coordination.preparation import prepare_example

                config = await prepare_example(
                    self.c, self.repository, args, effect_id("setup", call.call_id)
                )
                self.import_config(config, "已检查 " + args["adapter"] + " 执行环境")
                return ToolResult(self.setup())
            if call.name == "prepare_research":
                config = self.config()
                issues = validate_execution(config)
                if (
                    config.brief.objective in {"新的研究", "新研究"}
                    or not config.brief.instructions.strip()
                ):
                    issues.append("还需要明确研究目标和给 RSI 的要求")
                if args["execution_required"] and not (config.commands or config.verifier):
                    issues.append("本任务需要执行实验，请先接入实际执行命令")
                if issues:
                    return ToolResult({"ready": False, "issues": issues})
                if not args["summary"].strip():
                    raise ValueError("请先给用户一份简明的需求总结")
                self.c.state.setdefault("intake", {}).update(
                    status="ready",
                    summary=args["summary"],
                    prepared_at=now(),
                    execution_required=args["execution_required"],
                )
                self.c.save()
                self.c.store.event("research_ready", summary=args["summary"])
                self.c.enqueue(
                    "interaction",
                    "user",
                    args["summary"] + "\n\n你可以继续补充，或告诉我开始研究。",
                    key="prepare:" + call.call_id,
                )
                self.c.wait("interaction", "user")
                return ToolResult({"ready": True}, stop=True, reason="user")
            if call.name == "start_research":
                intake = self.c.state.get("intake", {})
                if intake.get("status") != "ready":
                    raise ValueError("请先整理需求并使用 prepare_research 交给用户确认")
                identifier = args["confirmation_message_id"]
                message = self.c.store.get("messages", identifier, ResearchMessage)
                if (
                    identifier not in self.c.current_message_ids("interaction")
                    or message.sender != "user"
                    or message.created_at <= (intake.get("prepared_at") or "")
                ):
                    raise ValueError("请等待用户对当前需求总结的明确回复")
                await self.start(identifier)
                return ToolResult({"started": True})
            raise ValueError("Unknown preparation tool")

        return handle
