"""Action contracts exchanged between RSIAgent and RSIController."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

ActionType: TypeAlias = Literal[
    "start_loop",
    "inspect_run",
    "resume_run",
    "stop_run",
]


@dataclass(slots=True)
class Action:
    """Structured action proposed by RSIAgent and executed by RSIController."""

    type: ActionType
    args: dict[str, Any] = field(default_factory=dict)
    requires_confirmation: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentResponse:
    """Response returned from the user-facing RSIAgent."""

    reply: str
    actions: list[Action] = field(default_factory=list)
    requires_confirmation: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
