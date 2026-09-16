"""Common DSH capabilities; role-specific coordination remains in ResearchActions."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from areal_rsi.coordination.actions import ResearchActions
from areal_rsi.modules.agent_runtime import ToolCall, ToolHandler, ToolResult
from areal_rsi.modules.dsh_tools import DshToolWorker, definitions, ensure_tools, tool_environment

TOOL_PROMPT = """日常文件操作和命令执行使用 bash、read、write、edit、glob、grep。
bash 每次是新 shell，默认在当前工作区；远端检查用非交互模式并设置超时。
允许联网时，使用 search_web 搜索、fetch_url 阅读网页。
研究员的创建、分工和消息传递统一使用 RSI 协作工具。
实验和长期作业通过配置的 run_job、wait_for_job 执行，不用后台 shell 绕过作业管理。
沿用已授权的环境，不打印凭证。作业结果未知时先检查实际状态，避免重复执行。
"""

CODEX_TOOL_PROMPT = """日常文件操作、命令执行和联网调研使用 Codex 原生工具。
允许联网时，直接使用 Codex 原生网页工具搜索与阅读页面。
研究员的创建、分工和消息传递统一使用 RSI 协作工具，不启动 Codex 原生 subagent 或独立 goal。
实验和长期作业通过配置的 run_job、wait_for_job 执行，不用后台 shell 绕过作业管理。
沿用已授权的环境，不打印凭证。
"""


def model_output(name: str, output: dict[str, Any]) -> dict[str, Any]:
    """Keep one shell output copy in context; the complete DSH receipt stays archived."""
    value = output.get("value")
    if (
        name == "bash"
        and isinstance(value, dict)
        and all(isinstance(value.get(stream), dict) for stream in ("stdout", "stderr"))
    ):
        # DSH renders the same stdout/stderr in content. Structured output also
        # preserves exit status, truncation and spill paths, without duplicating text.
        result = copy.deepcopy(output)
        result.pop("content", None)
        return result
    return output


class AgentTools:
    def __init__(
        self,
        actions: ResearchActions,
        root: Path,
        *,
        environment: Callable[[], Mapping[str, str]],
        credential_names: tuple[str, ...],
        instructions: str | None = None,
        backend: str = "standard",
    ):
        self.actions, self.root = actions, root
        self.environment, self.credential_names = environment, credential_names
        self.backend = backend
        self.configure(instructions)
        self.workers: dict[str, DshToolWorker] = {}
        self.installation: Path | None = None
        self.install_lock = asyncio.Lock()
        for tool in definitions() if backend == "standard" else []:
            actions.register(tool, self.handler, roles={"interaction", "rsi", "research"})

    def configure(self, instructions: str | None) -> None:
        self.tool_prompt = (
            (instructions + "\n" if instructions else "") + CODEX_TOOL_PROMPT
            if self.backend == "codex"
            else TOOL_PROMPT
            if instructions is None
            else instructions
        )

    def instructions(self, agent_id: str) -> str:
        return (
            self.tool_prompt
            + "\n你的默认工作区："
            + str(self.actions.workspace(agent_id).resolve())
        )

    async def _installation(self) -> Path:
        async with self.install_lock:
            if self.installation is None:
                self.installation = await asyncio.to_thread(ensure_tools, self.root)
            return self.installation

    def handler(self, agent_id: str) -> ToolHandler:
        async def handle(call: ToolCall) -> ToolResult:
            try:
                installation = await self._installation()
                if agent_id not in self.workers:
                    self.workers[agent_id] = DshToolWorker(
                        installation,
                        self.actions.workspace(agent_id),
                        self.actions.c.store.paths.agent(agent_id) / "tools",
                    )
                output = await self.workers[agent_id].execute(
                    call,
                    tool_environment(self.environment(), credential_names=self.credential_names),
                )
                return ToolResult(model_output(call.name, output))
            except (RuntimeError, OSError) as exc:
                return ToolResult({"error": str(exc), "isError": True})

        return handle

    async def close(self) -> None:
        await asyncio.gather(*(worker.close() for worker in self.workers.values()))
        self.workers.clear()
