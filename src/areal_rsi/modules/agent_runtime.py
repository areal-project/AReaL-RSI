"""Backend-neutral contract for one durable agent conversation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

Json = dict[str, Any]
EventSink = Callable[[Json], Awaitable[None]]


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: Json
    call_id: str
    recovered: bool = False


@dataclass(frozen=True)
class ToolResult:
    output: Any
    stop: bool = False
    reason: str = "waiting"


ToolHandler = Callable[[ToolCall], Awaitable[ToolResult]]


@dataclass(frozen=True)
class TurnResult:
    session_id: str
    turn_id: str
    text: str
    stop_reason: str
    usage: Json


class AgentRuntime(Protocol):
    backend: str

    def for_interaction(self) -> AgentRuntime: ...

    def has_session(self, session_dir: Path) -> bool: ...

    def configuration(self) -> Json: ...

    async def reconfigure_session(self, session_dir: Path, revision: str) -> None: ...

    async def close(self) -> None: ...

    async def turn(
        self,
        session_dir: Path,
        *,
        system_prompt: str,
        user_message: str | None,
        tools: Sequence[Json],
        handler: ToolHandler,
        message_id: str | None = None,
        event_sink: EventSink | None = None,
        recovery_state: Json | None = None,
        retry_id: str | None = None,
        incoming_messages: Callable[[], list[Json]] | None = None,
        advertised_tools: Sequence[Json] | None = None,
        workspace: Path | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> TurnResult: ...
