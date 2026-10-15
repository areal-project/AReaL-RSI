"""Domain-test doubles at the ResearchAgentRuntime boundary, with no alternate model loop."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from areal_rsi.api.interfaces import ToolCall, TurnResult


class ActionRuntime:
    """Make one application tool call, or stay pending until the app cancels us."""

    backend = "standard"

    def __init__(self, action: str | None = None):
        self.action = action

    def for_interaction(self) -> ActionRuntime:
        return ActionRuntime(self.action)

    def has_session(self, session_dir: Path) -> bool:
        return False

    def configuration(self) -> dict[str, Any]:
        return {"runtime": "domain-test-double"}

    async def reconfigure_session(self, session_dir: Path, revision: str) -> None:
        pass

    async def close(self) -> None:
        pass

    async def turn(self, session_dir: Path, **kwargs: Any) -> TurnResult:
        if self.action is None:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")
        message_id = kwargs.get("message_id") or "fixture"
        result = await kwargs["handler"](
            ToolCall(self.action, {"reason": "test fixture"}, message_id)
        )
        return TurnResult(session_dir.name, message_id, "", result.reason, {})


class CallbackRuntime(ActionRuntime):
    """Expose deliveries to scheduler tests; native persistence is tested separately."""

    def __init__(self, callback: Callable[[Path, dict[str, Any]], Awaitable[TurnResult]]):
        super().__init__()
        self.callback = callback
        self.calls: list[tuple[Path, dict[str, Any]]] = []
        self.sessions: set[Path] = set()

    def for_interaction(self) -> CallbackRuntime:
        return self

    def has_session(self, session_dir: Path) -> bool:
        return session_dir in self.sessions

    async def turn(self, session_dir: Path, **kwargs: Any) -> TurnResult:
        self.calls.append((session_dir, kwargs))
        self.sessions.add(session_dir)
        return await self.callback(session_dir, kwargs)
