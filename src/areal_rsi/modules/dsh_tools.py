"""Expose pinned DSH tools without replacing a durable Responses conversation."""

from __future__ import annotations

import asyncio
import copy
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from contextlib import suppress
from functools import lru_cache
from pathlib import Path
from typing import Any

from areal_rsi.modules.agent_runtime import ToolCall

RESOURCES = Path(__file__).resolve().parents[1] / "resources" / "dsh_tools"
SENSITIVE = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|AUTH|COOKIE", re.I)


def tool_environment(
    extra: Mapping[str, str] | None = None, *, credential_names: tuple[str, ...] = ()
) -> dict[str, str]:
    """Keep ordinary CLI/SSH configuration, without inheriting model credentials."""
    return {
        key: value
        for key, value in {**os.environ, **(extra or {})}.items()
        if key not in credential_names
        and (not SENSITIVE.search(key) or key == "SSH_AUTH_SOCK")
        and key not in {"BASH_ENV", "ENV", "NODE_OPTIONS", "NODE_PATH"}
        and not key.startswith("DSH_")
    }


def installation_path(root: Path) -> Path:
    digest = hashlib.sha256((RESOURCES / "package-lock.json").read_bytes()).hexdigest()[:16]
    return root.resolve() / "dsh-tools" / digest


def ensure_tools(root: Path) -> Path:
    """Install the exact npm lock once, outside the event loop and under a file lock."""
    node, npm = shutil.which("node"), shutil.which("npm")
    if not node or not npm:
        raise RuntimeError("DSH 基础工具需要 Node.js 22.19+（或 24+）和 npm；安装后重试即可。")
    directory = installation_path(root)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory.parent / ".install.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        marker = directory / ".ready"
        if marker.is_file() and (directory / "node_modules/@deepseek-ai/dsh-tools").is_dir():
            return directory
        for name in ("package.json", "package-lock.json"):
            shutil.copyfile(RESOURCES / name, directory / name)
        result = subprocess.run(
            [npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
            cwd=directory,
            env=tool_environment(),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if result.returncode:
            raise RuntimeError("DSH 工具依赖安装失败；请检查 Node/npm 和 npm registry 连接后重试。")
        marker.touch()
    return directory


@lru_cache(maxsize=1)
def native_schemas() -> list[dict[str, Any]]:
    """Snapshot generated from this composition, checked against the live registry."""
    schemas: list[dict[str, Any]] = json.loads((RESOURCES / "schemas.json").read_text())
    return schemas


DESCRIPTIONS = {
    "bash": "通过 DSH 执行 bash 命令，可调用 SSH、Git、Python 等本机程序。"
    "每次是独立 shell；默认在当前 Agent 的工作区执行，仅切换目录时传 workdir。"
    "默认 30 秒，上限 60 秒，超时会终止。"
    "长时间实验使用 run_job/wait_for_job；不要用 nohup、& 或轮询绕过作业管理。",
    "read": "读取 UTF-8 文件并显示行号。offset 从 1 开始；长文件用 offset/limit 分段读取。",
    "write": "创建或完整替换 UTF-8 文件。替换已有文件前先用 read 阅读，避免覆盖未读修改。",
    "edit": "精确替换文件中的文本。先 read；默认 old_string 必须只出现一次。",
    "glob": "按 glob 模式查找文件路径。默认搜索当前工作区，可用 path 指定其他目录。",
    "grep": "用 ripgrep 正则搜索文件内容，返回文件名、行号和匹配文本。",
}
PARAMETERS = {
    "command": "要执行的 bash 命令。",
    "description": "用简短中文说明命令目的。",
    "timeoutMs": "超时时间（毫秒），默认 30000，上限 60000。",
    "workdir": "命令工作目录，默认当前 Agent 的工作区；相对路径以该工作区为基准。",
    "file_path": "文件路径；相对路径以当前 Agent 的工作区为基准，也支持绝对路径。",
    "offset": "开始读取的行号，从 1 开始。",
    "limit": "最多读取 1000 行，默认 1000；返回文本另有 24 KB 上限，更多内容用 offset 分段读取。",
    "content": "完整的 UTF-8 文件内容。",
    "old_string": "要替换的原始文本，必须精确匹配。",
    "new_string": "替换后的文本；空字符串表示删除。",
    "replace_all": "是否替换全部匹配项，默认 false。",
    "path": "搜索的文件或目录，默认当前 Agent 的工作区。",
    "include": "筛选文件的单个 glob 模式，例如 *.py 或 *.{js,ts}。",
}


def definitions() -> list[dict[str, Any]]:
    schemas = copy.deepcopy(native_schemas())
    for schema in schemas:
        name = schema["name"]
        # Explicit non-strict mode preserves upstream's optional arguments.
        schema.update(type="function", strict=False, description=DESCRIPTIONS[name])
        if name == "read":
            schema["parameters"]["properties"]["limit"]["maximum"] = 1000
        for key, parameter in schema["parameters"]["properties"].items():
            parameter["description"] = (
                "文件路径 glob 模式，例如 **/*.py。"
                if key == "pattern" and name == "glob"
                else "ripgrep 正则表达式。"
                if key == "pattern"
                else PARAMETERS[key]
            )
    return schemas


class DshToolWorker:
    """One lazy tool process per agent, idle on stdin between calls (no model usage)."""

    def __init__(self, installation: Path, workspace: Path, state: Path):
        self.installation, self.workspace, self.state = installation, workspace.resolve(), state
        self.process: asyncio.subprocess.Process | None = None
        self.lock = asyncio.Lock()
        self.environment: dict[str, str] | None = None

    async def _start(self, environment: Mapping[str, str]) -> None:
        self.environment = dict(environment)
        self.workspace.mkdir(parents=True, exist_ok=True)
        temporary = self.state / "tmp"
        temporary.mkdir(parents=True, exist_ok=True)
        with (self.state / "worker.log").open("ab") as log:
            self.process = await asyncio.create_subprocess_exec(
                "node",
                str(RESOURCES / "worker.mjs"),
                str(self.installation),
                str(self.workspace),
                str(self.state.resolve()),
                cwd=self.workspace,
                env={**environment, "TMPDIR": str(temporary.resolve())},
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=log,
                limit=2 * 1024 * 1024,
            )
        assert self.process.stdout is not None
        packet = json.loads(await asyncio.wait_for(self.process.stdout.readline(), 30))
        if not packet.get("ready") or packet.get("schemas") != native_schemas():
            raise RuntimeError("DSH 工具注册结果与固定版本不一致，请检查安装。")

    async def execute(self, call: ToolCall, environment: Mapping[str, str]) -> dict[str, Any]:
        async with self.lock:
            try:
                if self.environment is not None and self.environment != environment:
                    await self.close()
                if self.process is None or self.process.returncode is not None:
                    await self._start(environment)
                assert self.process is not None
                assert self.process.stdin is not None and self.process.stdout is not None
                packet = {"id": call.call_id, "name": call.name, "arguments": call.arguments}
                self.process.stdin.write((json.dumps(packet, sort_keys=True) + "\n").encode())
                await self.process.stdin.drain()
                raw = await asyncio.wait_for(self.process.stdout.readline(), 75)
                reply = json.loads(raw)
                if reply.get("id") != call.call_id or not isinstance(reply.get("result"), dict):
                    raise RuntimeError("DSH 工具返回了无法匹配的调用结果。")
                result: dict[str, Any] = reply["result"]
                return result
            except asyncio.CancelledError:
                await self.close()
                raise
            except (OSError, ValueError, RuntimeError, TimeoutError):
                await self.close()
                # This may follow a side effect. The receipt will resolve a retry;
                # do not silently resend here or count an unknown effect as success.
                raise RuntimeError(
                    "DSH 工具进程中断，执行结果尚未确认。重试同一调用会读取已有回执；"
                    "若回执不完整，需要先检查实际状态，不会自动重复命令。"
                ) from None

    async def close(self) -> None:
        process, self.process = self.process, None
        if process is None or process.returncode is not None:
            return
        # DSH handles SIGTERM/EOF by aborting and joining owned subprocess trees.
        with suppress(ProcessLookupError):
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 8)
        except TimeoutError:
            with suppress(ProcessLookupError):
                process.kill()
            await process.wait()
