"""Check the production research runtime, tool bridge and restart receipts."""

from __future__ import annotations

import asyncio
import math
import secrets
from pathlib import Path
from typing import Any

from areal_rsi.base.paths import data_root
from areal_rsi.base.storage import new_id
from areal_rsi.coordination.actions import definition
from areal_rsi.coordination.config import ResearchConfig, config_from_dict
from areal_rsi.modules.agent_runtime import ToolCall, ToolResult
from areal_rsi.research_app import ResearchApp


async def preflight(
    config: ResearchConfig, *, root: Path | None = None, timeout: float = 180
) -> dict[str, Any]:
    """Use the same factory as ResearchApp; no alternate model transport or loop."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Preflight timeout must be finite and positive")
    config = config_from_dict(config.public())
    config.pin_model_settings()
    config.runtime()  # Validate before creating any files.
    directory = data_root(root) / "preflight" / new_id("session")
    workspace = directory / "workspace"
    word = secrets.token_hex(8)
    calls: list[ToolCall] = []
    events: list[dict[str, Any]] = []

    async def handle(call: ToolCall) -> ToolResult:
        calls.append(call)
        return ToolResult({"received": call.arguments}, stop=True, reason="acknowledged")

    async def emit(event: dict[str, Any]) -> None:
        events.append(event)

    tools = [definition("acknowledge", "Acknowledge a remembered value", value={"type": "string"})]
    prompt = (
        "Runtime smoke test. Remember the user's value in this conversation. "
        "Use the acknowledge task tool exactly once per turn, then finish. "
        "Do not write notes to files. Use the Runtime tool instructions below."
    )
    runtime = ResearchApp._runtime(config)
    try:
        async with asyncio.timeout(timeout):
            first = await runtime.turn(
                directory,
                system_prompt=prompt,
                user_message=f"Remember this value: {word}. Acknowledge ready.",
                message_id="first",
                tools=tools,
                handler=handle,
                event_sink=emit,
                workspace=workspace,
            )
            if first.stop_reason != "acknowledged" or len(calls) != 1:
                raise ValueError("The native runtime did not execute the acknowledge tool")
            binding_path = directory / "runtime/session-binding.json"
            binding = binding_path.read_bytes()
            await runtime.close()
            runtime = ResearchApp._runtime(config)
            second = await runtime.turn(
                directory,
                system_prompt=prompt,
                user_message="Acknowledge the value I asked you to remember.",
                message_id="second",
                tools=tools,
                handler=handle,
                event_sink=emit,
                workspace=workspace,
            )
            if (
                second.stop_reason != "acknowledged"
                or len(calls) != 2
                or calls[-1].arguments.get("value") != word
                or binding_path.read_bytes() != binding
            ):
                raise ValueError("The native session did not preserve its identity and memory")
            await runtime.close()
            runtime = ResearchApp._runtime(config)
            event_count = len(events)
            duplicate = await runtime.turn(
                directory,
                system_prompt=prompt,
                user_message="Acknowledge the value I asked you to remember.",
                message_id="second",
                tools=tools,
                handler=handle,
                event_sink=emit,
                workspace=workspace,
            )
            if duplicate != second or len(calls) != 2 or len(events) != event_count:
                raise ValueError("Duplicate delivery started another native turn or tool call")
        return {
            "backend": config.backend,
            "runtime": runtime.configuration(),
            "tool_calls": len(calls),
            "restart_continuation": True,
            "duplicate_delivery": "zero turns",
            "usage": [first.usage, second.usage],
            "records": str(directory),
        }
    except TimeoutError as exc:
        raise ValueError(f"Native runtime preflight timed out after {timeout:g} seconds") from exc
    finally:
        await runtime.close()
