"""Tool guidance only; native capabilities are supplied by AReaL Agent Runtime."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from areal_rsi.coordination.actions import ResearchActions
from areal_rsi.prompts import load_prompt

RUN_BOUNDARY_PROMPT = load_prompt("agents/shared/run_boundary.md")

TOOL_PROMPT = load_prompt("agents/shared/standard_tools.md")

CODEX_TOOL_PROMPT = load_prompt("agents/shared/codex_tools.md")


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
        execution_interface: str = "tools",
    ):
        self.actions, self.backend = actions, backend
        self.execution_interface = execution_interface
        self.supervisor_only = (
            actions.c.state.get("mode") == "passk" if supervisor_only is None else supervisor_only
        )
        self.configure(instructions)

    def configure(self, instructions: str | None) -> None:
        self.tool_prompt = (instructions + "\n" if instructions else "") + (
            load_prompt("agents/shared/codex_cli_tools.md")
            if self.backend == "codex" and self.execution_interface == "cli"
            else CODEX_TOOL_PROMPT if self.backend == "codex" else TOOL_PROMPT
        )

    def instructions(self, agent_id: str) -> str:
        if agent_id == "rsi" and self.supervisor_only:
            return load_prompt(
                "agents/shared/passk_supervisor_tools.md", run_boundary=RUN_BOUNDARY_PROMPT
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
