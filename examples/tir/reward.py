"""Task rewards: exact procedural integers and AReaL TIR's native math verifier."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from areal_rsi.api.records import TraceEvent


@lru_cache(maxsize=8)
def _native_tool_router(enabled_tools: str) -> Any:
    # Native backend boundary, imported only inside its runtime. Replay routing
    # against the actual configured tools without executing any recorded call.
    from tool_manager import ToolRegistry, ToolRouter  # type: ignore[import-not-found]

    return ToolRouter(ToolRegistry(enabled_tools=enabled_tools))


def native_reward_input(
    events: tuple[TraceEvent, ...], *, enabled_tools: str = "python;calculator"
) -> str:
    """Reconstruct the exact native completion, including formatted tool output."""
    from tir_workflow import TIRWorkflow  # type: ignore[import-not-found]

    if not events or events[0].kind != "prompt":
        raise ValueError("Reward replay requires an initial recorded prompt")
    router = _native_tool_router(enabled_tools)
    parts = []
    pending = False
    routed = False
    generations = 0
    for index, event in enumerate(events):
        if event.kind == "prompt":
            if index != 0:
                raise ValueError("Unexpected prompt inside recorded completion")
        elif event.kind == "generation":
            if pending:
                raise ValueError("Recorded tool call is missing its result")
            parts.append(event.content)
            generations += 1
        elif event.kind == "tool_call":
            if pending or generations == 0:
                raise ValueError("Recorded tool calls are out of order")
            pending = True
            routed = router.route(event.content) is not None
        elif event.kind == "tool_result":
            if not pending:
                raise ValueError("Recorded tool result has no preceding call")
            if routed:
                parts.append(TIRWorkflow._process_tool_result(event.content))
            pending = False
    if pending or generations == 0:
        raise ValueError("Recorded completion is incomplete")
    return "".join(parts)


def integer_reward(
    prompt: str,
    completions: str,
    prompt_ids: object,
    completion_ids: object,
    answer: str,
    **kwargs: object,
) -> float:
    matches = re.findall(r"\\boxed\s*\{\s*([+-]?\d[\d,]*)\s*\}", completions)
    return float(bool(matches) and int(matches[-1].replace(",", "")) == int(answer))


def math_reward(
    prompt: str,
    completions: str,
    prompt_ids: object,
    completion_ids: object,
    answer: str,
    **kwargs: object,
) -> float:
    from areal.reward import get_math_verify_worker  # type: ignore[import-not-found]

    return float(get_math_verify_worker().verify(str(completions), str(answer)))
