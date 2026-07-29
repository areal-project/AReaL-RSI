"""RSIAgent contract."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from areal_rsi.schemas.actions import Action, AgentResponse
from areal_rsi.schemas.core import MemoryContext

if TYPE_CHECKING:
    from areal_rsi.controller.run_state import RunState


class RSIAgent(Protocol):
    """User-facing coordinator agent for AReaL-RSI."""

    def chat(
        self,
        message: str,
        *,
        run_id: str | None = None,
        controller: object,
        memory: object,
        workflow_spec: dict[str, Any],
    ) -> AgentResponse:
        """Return a reply and zero or more structured actions."""

    def propose_next_action(
        self,
        *,
        run_state: "RunState",
        memory_context: MemoryContext,
        workflow_spec: dict[str, Any],
    ) -> Action:
        """Propose the next action from current state and memory."""

    def explain(
        self,
        *,
        run_id: str,
        controller: object,
        memory: object,
        question: str,
    ) -> str:
        """Explain a run decision, state, or artifact."""
