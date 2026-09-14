"""Common DSH capabilities; role-specific coordination remains in ResearchActions."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from areal_rsi.coordination.actions import ResearchActions
from areal_rsi.modules.dsh_tools import DshToolWorker, definitions, ensure_tools, tool_environment
from areal_rsi.modules.responses import ToolCall, ToolHandler, ToolResult

TOOL_PROMPT = """bash、read、write、edit、glob、grep 用于日常工作。
bash 可使用已有 SSH、Git、Python。
bash 每次是新 shell，默认在当前工作区；远端检查用非交互模式并设置超时。
实验和长期作业使用作业工具，结果与产物可直接读取、收集或复测，无需手抄记录。
沿用已授权的认证，不打印凭证。OUTCOME_UNKNOWN 先检查实际结果，避免重复执行。
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
    ):
        self.actions, self.root = actions, root
        self.environment, self.credential_names = environment, credential_names
        self.tool_prompt = TOOL_PROMPT if instructions is None else instructions
        self.workers: dict[str, DshToolWorker] = {}
        self.installation: Path | None = None
        self.install_lock = asyncio.Lock()
        for tool in definitions():
            actions.register(tool, self.handler, roles={"interaction", "rsi", "research"})

    def instructions(self, agent_id: str) -> str:
        return (
            self.tool_prompt
            + (
                "\nRSI 使用工具只为调研、核查证据、协调与收尾；候选实现和实验循环交给研究员。"
                if agent_id == "rsi"
                else ""
            )
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
