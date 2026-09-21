"""前台 TUI 和后台 SDK 共用的 DSH 安装与版本选择。"""

from __future__ import annotations

import fcntl
import json
import shutil
import subprocess
from pathlib import Path

DSH_VERSION = "0.1.2-rc.1"


def _require_compatible_node() -> Path:
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("DSH CLI/TUI 需要 Node.js 22.19+（或 24+）和 pnpm")
    check = subprocess.run(
        [
            node,
            "-e",
            "const {parseEnv}=require('node:util');"
            "if(typeof parseEnv !== 'function') process.exit(1)",
        ],
        text=True,
        capture_output=True,
    )
    if check.returncode:
        version = subprocess.run([node, "--version"], text=True, capture_output=True).stdout.strip()
        raise RuntimeError(f"DSH CLI/TUI 需要 Node.js 22.19+（或 24+）；当前是 {version or node}")
    return Path(node).resolve()


def ensure_cli(root: Path) -> Path:
    """按数据目录复用安装；文件锁防止多个 worker 同时写入依赖目录。"""
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".dsh-cli.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _ensure_cli(root)


def _ensure_cli(root: Path) -> Path:
    """检查并安装前后台共用的固定版本 DSH CLI。"""
    _require_compatible_node()
    directory = root.resolve() / "dsh-cli"
    installed = directory / "node_modules" / "@deepseek-ai/dsh" / "package.json"
    binary = directory / "node_modules" / ".bin" / "dsh"
    if (
        installed.exists()
        and binary.is_file()
        and json.loads(installed.read_text()).get("version") == DSH_VERSION
    ):
        return binary
    if shutil.which("pnpm") is None:
        raise RuntimeError("首次安装 DSH CLI/TUI 需要 Node.js 22.19+（或 24+）和 pnpm")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "package.json").write_text(
        json.dumps(
            {
                "name": "rsi-dsh-cli",
                "private": True,
                "dependencies": {"@deepseek-ai/dsh": DSH_VERSION},
            },
            indent=2,
        )
        + "\n"
    )
    print(f"安装项目内 DSH CLI {DSH_VERSION}（仅首次运行）…", flush=True)
    subprocess.run(
        ["pnpm", "--dir", str(directory), "install", "--ignore-scripts"], check=True, timeout=180
    )
    if not binary.is_file():
        raise RuntimeError("DSH CLI 安装未完成，请检查安装输出后重试")
    return binary
