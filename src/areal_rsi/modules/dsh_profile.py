"""安装并检查 RSI 使用的固定版本 DSH TUI profile。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from areal_rsi.modules.dsh import DSH_VERSION, ensure_cli
from areal_rsi.modules.dsh_config import PERSONA

TUI_PACKAGE = "@deepseek-harness-tui/dsh-tui"
TUI_VERSION = "0.10.0-beta.5"
# 与旧插件的试验 profile 分开，保留已有对话，不混装两种 TUI。
PROFILE = "rsi-chimney"


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
    """启动时同步包内 Persona，保留已有 preset 中的工具和其他配置。"""
    destination = home / ".agent-presets" / "rsi"
    output = destination / "agent.cordis.yml"
    if output.exists():
        source = output
    else:
        sources = list(
            (root.resolve() / "dsh-cli/node_modules/.pnpm").glob(
                f"@deepseek-ai+dsh-agent-presets@{DSH_VERSION}*/node_modules/"
                "@deepseek-ai/dsh-agent-presets/presets/standard/agent.cordis.yml"
            )
        )
        if len(sources) != 1:
            raise RuntimeError("未找到唯一的固定版本 DSH standard preset，请重新准备 CLI")
        source = sources[0]
    source_text = source.read_text()
    # 保留 DSH 的 !!js 标签；不求值或丢弃已有工具配置中的表达式。
    tree = yaml.compose(source_text)
    if not isinstance(tree, yaml.SequenceNode):
        raise RuntimeError("DSH preset 必须为配置列表")
    for row in tree.value:
        if not isinstance(row, yaml.MappingNode):
            raise RuntimeError("DSH preset 配置项必须为对象")
        if any(k.value == "id" and v.value == "persona" for k, v in row.value):
            config = next((v for k, v in row.value if k.value == "config"), None)
            if not isinstance(config, yaml.MappingNode):
                raise RuntimeError("DSH preset 的 persona.config 必须为对象")
            config.value = [
                (key, value) for key, value in config.value if key.value != "text"
            ] + [
                (
                    yaml.ScalarNode("tag:yaml.org,2002:str", "text"),
                    yaml.ScalarNode("tag:yaml.org,2002:str", PERSONA, style="|"),
                )
            ]
            break
    else:
        raise RuntimeError("DSH preset 缺少 persona")
    destination.mkdir(parents=True, exist_ok=True)
    rendered = yaml.serialize(tree, allow_unicode=True)
    if not output.exists() or rendered != source_text:
        output.write_text(rendered)
    (destination / "preset.yml").write_text(
        "name: Interaction Agent\ndescription: 日常研发与 RSI 实验入口\n"
    )
    return output
