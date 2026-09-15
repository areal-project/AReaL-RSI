"""本地 Harness 快照构建和 DSH 任务执行。"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from areal_rsi.api.base import (
    AgentSpec,
    ContentRef,
    HarnessChange,
    HarnessVersion,
    JsonObject,
    SolutionVersion,
    Task,
    TaskResult,
    TrajectoryRef,
)
from areal_rsi.api.modules import AgentSession, ConfiguredAgentRuntime
from areal_rsi.base.storage import (
    DIRECTORY_TYPE,
    FileContentStore,
    new_id,
    safe_name,
    safe_relative,
)


def editable(name: str) -> bool:
    path = safe_relative(name)
    return name in ("prompt.md", "mcp.json") or (
        path.parts[0] in ("skills", "tools") and path.suffix in (".md", ".py", ".json")
    )


def validate_harness(directory: Path) -> None:
    if not (directory / "prompt.md").is_file():
        raise ValueError("Harness 缺少 prompt.md")
    for file in directory.rglob("*"):
        if file.is_symlink():
            raise ValueError("Harness 不允许符号链接")
        if file.is_file():
            if (
                not editable(file.relative_to(directory).as_posix())
                or file.stat().st_size > 100_000
            ):
                raise ValueError("只允许修改 prompt、skill、tool 和 mcp，单文件最多 100 KB")
            if file.suffix == ".py":
                compile(file.read_text(), str(file), "exec")
    config = json.loads((directory / "mcp.json").read_text())
    for server in config["servers"]:
        if set(server) - {"name", "script", "enabled"}:
            raise ValueError("MVP MCP 只接受 name/script/enabled")
        if not str(server["script"]).startswith("tools/"):
            raise ValueError("MCP 必须运行 Harness tools 目录中的本地脚本")
        safe_relative(server["script"])
        if not (directory / server["script"]).is_file():
            raise ValueError("MCP 脚本不存在")


class LocalHarnessBuilder:
    def __init__(self, content: FileContentStore, root: Path):
        self.content = content
        self.root = root

    async def build(self, base: HarnessVersion, change: HarnessChange) -> ContentRef:
        self.root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.root) as temporary:
            directory = Path(temporary) / "harness"
            await self.content.materialize(base.content, directory)
            patch = await self.content.read_bytes(change.patch)
            if not patch.strip():
                raise ValueError("拒绝空 patch")
            # git apply 校验路径穿越并在全部 hunk 合法后应用；不修改用户 checkout。
            check = await asyncio.to_thread(
                subprocess.run,
                ["git", "apply", "--no-index", "--check", "-"],
                input=patch,
                cwd=directory,
                capture_output=True,
            )
            if check.returncode:
                raise ValueError(f"无法应用候选 patch: {check.stderr.decode()[:1500]}")
            applied = await asyncio.to_thread(
                subprocess.run,
                ["git", "apply", "--no-index", "-"],
                input=patch,
                cwd=directory,
                capture_output=True,
            )
            if applied.returncode:
                raise ValueError(applied.stderr.decode()[:1500])
            validate_harness(directory)
            content = await self.content.snapshot(directory)
            if content.digest == base.content.digest:
                raise ValueError("候选未改变 Harness 内容")
            return content


class LocalHarnessRuntime:
    def __init__(
        self, runtime: ConfiguredAgentRuntime, content: FileContentStore, workspaces: Path
    ):
        self.runtime = runtime
        self.content = content
        self.workspaces = workspaces

    async def _input_message(self, task: Task, workspace: Path) -> str:
        if not isinstance(task.input, ContentRef):
            return json.dumps(task.input, ensure_ascii=False)
        path = workspace / "task-input"
        is_directory = task.input.media_type == DIRECTORY_TYPE
        if is_directory:
            await self.content.materialize(task.input, path)
        else:
            path.write_bytes(await self.content.read_bytes(task.input))
        # 只传工作区内相对路径，不把整个文件塞入上下文，也不暴露远端 URI。
        return json.dumps(
            {
                "input_path": "task-input",
                "input_kind": "directory" if is_directory else "file",
                "media_type": task.input.media_type,
                "instruction": "公开任务输入位于此工作区路径，请按需分段读取后完成任务。",
            },
            ensure_ascii=False,
        )

    async def _artifact_inputs(
        self, solution: SolutionVersion, workspace: Path
    ) -> list[JsonObject]:
        items: list[JsonObject] = []
        for artifact in solution.artifacts:
            path = workspace / "artifacts" / safe_name(artifact.version_id)
            if artifact.content.media_type == DIRECTORY_TYPE:
                await self.content.materialize(artifact.content, path)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(await self.content.read_bytes(artifact.content))
            items.append(
                {
                    "name": artifact.name,
                    "version_id": artifact.version_id,
                    "path": path.relative_to(workspace).as_posix(),
                }
            )
        return items

    async def run(
        self, solution: SolutionVersion, task: Task, *, config: JsonObject | None = None
    ) -> TaskResult:
        workspace = self.workspaces / new_id("task")
        await self.content.materialize(solution.harness.content, workspace)
        validate_harness(workspace)
        message = await self._input_message(task, workspace)
        if solution.artifacts:
            message = json.dumps(
                {
                    "task_input": json.loads(message),
                    "artifact_inputs": await self._artifact_inputs(solution, workspace),
                    "instruction": "这些产物是当前 Solution 的输入快照，请从工作区按需读取。",
                },
                ensure_ascii=False,
            )
        prompt = await self.content.put_text((workspace / "prompt.md").read_text())
        skills = tuple(
            [
                await self.content.put_text(p.read_text())
                for p in sorted((workspace / "skills").glob("*.md"))
            ]
        )
        mcp_config = json.loads((workspace / "mcp.json").read_text())
        servers: list[str] = []
        session: AgentSession | None = None
        ref: TrajectoryRef | None = None
        try:
            for server in mcp_config["servers"]:
                if server.get("enabled", True):
                    key = new_id("mcp")
                    self.runtime.mcp_registry[key] = {
                        "serverName": server["name"],
                        "transport": "stdio",
                        "command": sys.executable,
                        "args": [str(workspace / server["script"])],
                        "cwd": str(workspace),
                        "failOnStartupError": True,
                    }
                    servers.append(key)
            spec = AgentSpec(
                "task-agent",
                getattr(self.runtime, "runtime_id", "deepseek-harness@v1"),
                solution.model,
                (prompt,),
                skills,
                ("bash@v1",),
                tuple(servers),
            )
            session = await self.runtime.open(spec, workspace_root=str(workspace))
            response = await session.send(message)
        finally:
            try:
                if session is not None:
                    ref = await session.finish()
            finally:
                for key in servers:
                    self.runtime.mcp_registry.pop(key, None)
        assert ref is not None
        output: Any = {
            "task_id": task.task_id,
            "solution_id": solution.solution_id,
            "model_version_id": solution.model.version_id,
            "harness_digest": solution.harness.content.digest,
            "response": response,
        }
        return TaskResult(await self.content.put_json(output), ref)
