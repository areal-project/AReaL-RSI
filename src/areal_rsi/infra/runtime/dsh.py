"""Install the shared DSH CLI and its matching Runtime profile under process locks."""

from __future__ import annotations

import fcntl
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

DSH_VERSION = "0.1.2-rc.1"


def ensure_cli(root: Path) -> Path:
    """按数据目录复用安装；文件锁防止多个 worker 同时写入依赖目录。"""
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".dsh-cli.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _ensure_cli(root)


def _ensure_cli(root: Path) -> Path:
    """检查并安装前后台共用的固定版本 DSH CLI。"""
    directory = root.resolve() / "dsh-cli"
    installed = directory / "node_modules" / "@deepseek-ai/dsh" / "package.json"
    binary = directory / "node_modules" / ".bin" / "dsh"
    if (
        installed.exists()
        and binary.is_file()
        and json.loads(installed.read_text()).get("version") == DSH_VERSION
    ):
        return binary
    if shutil.which("pnpm") is None or shutil.which("node") is None:
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


def ensure_profile(root: Path) -> Path:
    archive = Path(__file__).parents[2] / "resources/runtime/areal-agent-runtime-dsh-0.3.0.tgz"
    digest = hashlib.sha256(
        archive.read_bytes() + (archive.parent / "pnpm-lock.yaml").read_bytes()
    ).hexdigest()
    home = root.resolve() / "runtime-dsh" / digest[:16]
    home.mkdir(parents=True, exist_ok=True)
    with (home / ".install.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        profile = home / "profiles/agent-runtime"
        marker = home / ".ready"
        if marker.exists() and marker.read_text() == digest:
            return home
        if not shutil.which("pnpm"):
            raise RuntimeError("DSH Runtime profile requires pnpm and Node.js 22.19+ / 24+")
        profile.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(archive, profile / "runtime.tgz")
        (profile / "package.json").write_text(
            json.dumps(
                {
                    "name": "rsi-agent-runtime-profile",
                    "private": True,
                    "dsh": {
                        "profile": {
                            "bundles": [
                                "@deepseek-ai/dsh-base",
                                "@deepseek-ai/dsh-sdk-app",
                                "@areal/agent-runtime-dsh",
                            ],
                            "patchReload": "startup",
                        }
                    },
                    "dependencies": {"@areal/agent-runtime-dsh": "file:runtime.tgz"},
                }
            )
        )
        (profile / "cordis.yml").write_text("[]\n")
        (profile / "cordis.patch.yml").write_text("[]\n")
        shutil.copyfile(archive.parent / "pnpm-lock.yaml", profile / "pnpm-lock.yaml")
        with (home / "install.log").open("wb") as log:
            subprocess.run(
                ["pnpm", "install", "--frozen-lockfile", "--ignore-scripts"],
                cwd=profile,
                check=True,
                timeout=180,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        marker.write_text(digest)
        return home
