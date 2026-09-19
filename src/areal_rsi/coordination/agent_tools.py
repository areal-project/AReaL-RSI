"""Tool guidance only; native capabilities are supplied by AReaL Agent Runtime."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from areal_rsi.agents.communication import RUN_BOUNDARY_PROMPT
from areal_rsi.coordination.actions import ResearchActions

TOOL_PROMPT = """日常文件操作和命令执行使用 Runtime 提供的 workspace 工具。
允许联网时，使用 search_web 搜索、fetch_url 阅读网页。
实验和长期作业通过 run_job、wait_for_job 执行。沿用已配置的环境，不打印凭证。
"""

CODEX_TOOL_PROMPT = """日常文件操作、命令执行和联网调研使用 Codex 原生工具。
允许联网时，使用 Codex 原生网页工具搜索与阅读页面。
不要启动 Codex 原生 subagent 或独立 goal。
RSI 任务工具的调用方法见下方 Runtime 说明；实验和长期作业通过 run_job、wait_for_job 执行。
沿用已配置的环境，不打印凭证。
"""


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
        supervisor_only: bool | None = None,
    ):
        self.actions, self.backend = actions, backend
        self.supervisor_only = (
            actions.c.state.get("mode") == "passk" if supervisor_only is None else supervisor_only
        )
        self.configure(instructions)

    def configure(self, instructions: str | None) -> None:
        self.tool_prompt = (instructions + "\n" if instructions else "") + (
            CODEX_TOOL_PROMPT if self.backend == "codex" else TOOL_PROMPT
        )

    def instructions(self, agent_id: str) -> str:
        if agent_id == "rsi" and self.supervisor_only:
            return (
                RUN_BOUNDARY_PROMPT + "\n"
                "先用 start_researchers 启动团队；用 get_state 查看进展，"
                "用 continue_researcher 提醒已停下来的研究员继续。\n"
                "没有需要处理的事项时用 wait 等待新报告。只使用这些监督工具。"
                "RSI 工具的调用方法见下方 Runtime 说明。"
            )
        return (
            RUN_BOUNDARY_PROMPT
            + "\n"
            + self.tool_prompt
            + "\n你的默认工作区："
            + str(self.actions.workspace(agent_id).resolve())
        )

    async def close(self) -> None:
        pass
