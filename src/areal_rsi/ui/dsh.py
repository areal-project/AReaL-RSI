"""薄启动器：组合现成 DSH TUI 与 RSI MCP，不实现聊天或 Agent loop。"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from areal_rsi.config import ModelConfig
from areal_rsi.modules.dsh import DSH_VERSION, ensure_cli
from areal_rsi.modules.gateway import GatewayProxy

TUI_PACKAGE = "@deepseek-harness-tui/dsh-tui"
TUI_VERSION = "0.10.0-beta.5"
# 与旧插件的试验 profile 分开，保留已有对话，不混装两种 TUI。
PROFILE = "rsi-chimney"
PERSONA = """你是 RSI Agent，是用户日常研发和自迭代实验的入口。使用中文自然对话。
普通对话直接回复，不要强制输出 JSON，不要再启动一个聊天模型转述用户问题。
用户要求实验时，使用 RSI MCP 工具：先 rsi_plan，展示工具返回的实际模型、目标、评测集、研究线和轮数。
模型、评测集和研究线数量由启动配置固定，写进 goal 不会改变它们。
实际配置不符时应说明并让用户重启正确配置。
配置错误应根据工具的具体提示解决，不要推测缺少实验模板，也不要向用户索要不存在的模板名。
仅当用户明确确认该计划后，才调用 rsi_start 并设置 confirmed=true；不能自行代替用户确认。
未指定轮数时先询问。工具返回 run_id 表示已提交，不代表成功；用 rsi_status 查询真实聚合状态。
停止和恢复也必须基于用户明确要求。长实验在独立后台运行，退出当前对话不会终止它。
不要在对话中手写研究循环、改变评分、向 Harness 注入题目答案或修改运行记录。
不要自行直接启动 rsi run；实验统一经由 RSI MCP 控制，详细进度由 RSI Web 面板展示。
ResearchAgent 和 Task Agent 的执行、版本管理、Memory、轨迹与评测由 Python 后端负责。
"""


class _EnvironmentExpression(str):
    """DSH 在加载配置时求值的表达式，文件内只保存变量名。"""


yaml.SafeDumper.add_representer(
    _EnvironmentExpression,
    lambda dumper, value: dumper.represent_scalar("tag:yaml.org,2002:js", str(value)),
)

MCP_ENVIRONMENT = (
    "RSI_MODEL",
    "RSI_BASE_URL",
    "RSI_API_KEY",
    "RSI_DATA_ROOT",
    "RSI_BENCHMARK",
    "RSI_RESEARCH_DIRECTIONS",
    "RSI_GSM8K_DATA",
    "RSI_TRAIN_LIMIT",
    "RSI_VALIDATE_LIMIT",
    "RSI_TURN_TIMEOUT",
)


def ensure_profile(root: Path) -> tuple[Path, Path]:
    home = root.resolve() / "dsh-home"
    manifest = home / "profiles" / PROFILE / "package.json"
    binary = ensure_cli(root)
    configured = json.loads(manifest.read_text()) if manifest.exists() else {}
    plugin = manifest.parent / "node_modules" / TUI_PACKAGE / "cordis.patch.yml"
    if configured.get("dependencies", {}).get(TUI_PACKAGE) != TUI_VERSION or not plugin.exists():
        if shutil.which("pnpm") is None:
            raise RuntimeError("首次安装 DSH TUI 插件需要 pnpm；请安装 pnpm 后重新执行 rsi tui")
        print(f"安装 DSH TUI 插件 {TUI_PACKAGE}@{TUI_VERSION}（仅首次运行）…", flush=True)
        environment = {**os.environ, "DSH_HOME": str(home), "DSH_TELEMETRY_DISABLED": "1"}
        subprocess.run(
            [str(binary), "plugin", "--profile", PROFILE, "add", f"{TUI_PACKAGE}@{TUI_VERSION}"],
            env=environment,
            check=True,
            timeout=180,
        )
        configured = json.loads(manifest.read_text())
    bundles = configured.get("dsh", {}).get("profile", {}).get("bundles", [])
    if "@deepseek-ai/dsh-base" not in bundles or TUI_PACKAGE not in bundles:
        raise RuntimeError("DSH profile 缺少基础或 TUI bundle，请检查该 profile 的 package.json")
    ensure_rsi_preset(root, home)
    return binary, home


def ensure_rsi_preset(root: Path, home: Path) -> Path:
    """使用 DSH 的 preset 接入协议，保留标准研发工具，只替换入口角色提示。"""
    destination = home / ".agent-presets" / "rsi"
    output = destination / "agent.cordis.yml"
    if output.exists():
        return output  # 后续允许用户直接编辑自己的 preset，不在启动时覆盖。
    sources = list(
        (root.resolve() / "dsh-cli/node_modules/.pnpm").glob(
            f"@deepseek-ai+dsh-agent-presets@{DSH_VERSION}*/node_modules/"
            "@deepseek-ai/dsh-agent-presets/presets/standard/agent.cordis.yml"
        )
    )
    if len(sources) != 1:
        raise RuntimeError("未找到唯一的固定版本 DSH standard preset，请重新准备 CLI")
    tree = yaml.compose(sources[0].read_text())
    if not isinstance(tree, yaml.SequenceNode):
        raise RuntimeError("无法识别 DSH standard preset")
    for row in tree.value:
        if any(k.value == "id" and v.value == "persona" for k, v in row.value):
            for key, config in row.value:
                if key.value == "config":
                    config.value = [
                        (
                            yaml.ScalarNode("tag:yaml.org,2002:str", "text"),
                            yaml.ScalarNode("tag:yaml.org,2002:str", PERSONA, style="|"),
                        )
                    ]
            break
    else:
        raise RuntimeError("DSH standard preset 缺少 persona")
    destination.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.serialize(tree, allow_unicode=True))
    (destination / "preset.yml").write_text(
        "name: RSI Agent\ndescription: 日常研发与 RSI 实验入口\n"
    )
    return output


def write_profile_patch(root: Path, workspace: Path, model: str) -> Path:
    patch = root.resolve() / "dsh-entry.patch.yml"
    patch.parent.mkdir(parents=True, exist_ok=True)
    tree = [
        {
            "id": "dsh-tui",
            "config": {
                "provider": "deepseek-official",
                "model": model,
                "cwd": str(workspace.resolve()),
                "effort": "off",
                "preset": "rsi",
                "fullscreen": True,
            },
        },
        {"id": "system-prompt", "config": {"persona": PERSONA}},
        {
            "id": "llm-deepseek",
            "config": {
                "thinking": "disabled",
                "maxTokens": 8192,
                "defaultContextWindow": int(os.environ.get("RSI_CONTEXT_WINDOW", "32768")),
                # TUI 会验证模型目录；必须注册用户模型，避免退回内置 DeepSeek 模型。
                "models": [{"id": model}],
            },
        },
        {
            # 部分兼容模型会把状态行和正文合并为一行。TUI 会剥掉以 ⏵ 开头的
            # 整行，最终看起来像没有回复；关闭状态行提示可保留完整模型输出。
            "id": "working-activity",
            "config": {
                "publish": False,
                "publishIntervalMs": 500,
                "narrate": False,
            },
        },
        # RSI 使用已配置的模型网关，不需要订阅 OAuth 或额外的标题模型。
        {"id": "dsh-tui-auth", "disabled": True},
        {"id": "session-title-llm", "disabled": True},
        {
            "insert": [
                {
                    "id": "rsi-mcp",
                    "name": "@deepseek-ai/dsh-mcp-client",
                    "config": {
                        "serverName": "rsi",
                        "transport": "stdio",
                        "command": sys.executable,
                        "args": ["-m", "areal_rsi", "_mcp", "--root", str(root.resolve())],
                        "cwd": str(workspace.resolve()),
                        # DSH 默认清理子进程凭证；仅向 RSI MCP 显式传递所需配置。
                        "env": _EnvironmentExpression(
                            "Object.fromEntries("
                            + json.dumps(MCP_ENVIRONMENT)
                            + ".flatMap(key => process.env[key] === undefined "
                            "? [] : [[key, process.env[key]]]))"
                        ),
                        "failOnStartupError": True,
                    },
                }
            ]
        },
    ]
    patch.write_text(yaml.safe_dump(tree, allow_unicode=True, sort_keys=False))
    return patch


def launch_tui(root: Path, *, resume: str | None = None, setup_only: bool = False) -> int:
    model_config = ModelConfig.from_environment()
    binary, home = ensure_profile(root)
    patch = write_profile_patch(root, Path.cwd(), model_config.model)
    if setup_only:
        print(f"DSH TUI profile 已准备：{home / 'profiles' / PROFILE}")
        return 0
    proxy = GatewayProxy(model_config.base_url, model_config.api_key)
    command = [
        str(binary),
        "--profile",
        PROFILE,
        "--patch",
        str(patch),
    ]
    if resume:
        command += ["--resume", resume]
    environment = {
        **os.environ,
        "DSH_HOME": str(home),
        "DSH_TELEMETRY_DISABLED": "1",
        "DSH_TELEMETRY_MODE": "DISABLED",
        "DSH_TUI_LANG": "zh",
        "DEEPSEEK_BASE_URL": proxy.url,
        "DEEPSEEK_API_KEY": "local-proxy",
    }
    previous = signal.getsignal(signal.SIGINT)
    child: subprocess.Popen[bytes] | None = None
    try:
        child = subprocess.Popen(command, cwd=Path.cwd(), env=environment)
        # DSH 直接接管终端；Ctrl+C 交给它处理，Python 不重新实现输入行为。
        signal.signal(signal.SIGINT, lambda *_: None)
        return child.wait()
    finally:
        signal.signal(signal.SIGINT, previous)
        if child is not None and child.poll() is None:
            child.terminate()
            child.wait(timeout=10)
        proxy.close()
        if os.environ.get("RSI_TUI_AUDIT") == "1":
            # 显式诊断开关：含对话/工具原文，仅写入本地，不含代理持有的 API Key。
            (root.resolve() / "tui-gateway-audit.json").write_text(
                json.dumps(proxy.requests, ensure_ascii=False)
            )
