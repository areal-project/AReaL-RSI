"""Check the configured model, tools and continuous sessions without GPU resources.

Run: uv run --env-file .env python scripts/preflight_responses.py
Credentials are read only from the environment, never printed or archived.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from areal_rsi.base.storage import new_id
from areal_rsi.modules.responses import ResponsesConfig, ResponsesRuntime, ToolCall, ToolResult


async def main() -> None:
    key = os.environ.get("RSI_API_KEY", "")
    if not key:
        raise SystemExit("Set RSI_API_KEY in the environment or an ignored .env file")
    runtime = ResponsesRuntime(
        ResponsesConfig(
            base_url=os.environ.get("RSI_BASE_URL", "https://api.openai.com"),
            api_key=key,
            model=os.environ.get("RSI_MODEL", "gpt-5.6-luna"),
            reasoning_effort=os.environ.get("RSI_REASONING_EFFORT", "xhigh"),
            max_output_tokens=4096,
            max_model_calls_per_turn=3,
            timeout=180,
        )
    )
    directory = (
        Path(os.environ.get("RSI_DATA_ROOT", ".rsi/local")) / "preflight" / new_id("session")
    )
    calls: list[ToolCall] = []

    async def handle(call: ToolCall) -> ToolResult:
        calls.append(call)
        return ToolResult({"received": call.arguments}, stop=True, reason="acknowledged")

    definition = {
        "type": "function",
        "name": "acknowledge",
        "description": "Acknowledge a remembered value",
        "parameters": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        "strict": True,
    }
    prompt = "Protocol smoke test. Use acknowledge as requested; its result ends the turn."
    first = await runtime.turn(
        directory,
        system_prompt=prompt,
        user_message="Remember the word cedar. Acknowledge ready.",
        message_id="first",
        tools=[definition],
        handler=handle,
    )
    second = await runtime.turn(
        directory,
        system_prompt=prompt,
        user_message="Acknowledge the word I asked you to remember.",
        message_id="second",
        tools=[definition],
        handler=handle,
    )
    count = len(calls)
    duplicate = await runtime.turn(
        directory,
        system_prompt=prompt,
        user_message="Acknowledge the word I asked you to remember.",
        message_id="second",
        tools=[definition],
        handler=handle,
    )
    if first.stop_reason != "acknowledged" or second.stop_reason != "acknowledged":
        raise SystemExit("Provider did not execute the requested tools")
    if not calls or calls[-1].arguments.get("value", "").lower() != "cedar":
        raise SystemExit("Session continuation did not preserve the instruction")
    if len(calls) != count or duplicate != second:
        raise SystemExit("Duplicate delivery was not deduplicated")
    print(
        json.dumps(
            {
                "model": runtime.config.model,
                "reasoning_effort": runtime.config.reasoning_effort,
                "tool_calls": count,
                "continuous_context": True,
                "duplicate_delivery": "zero requests",
                "usage": [first.usage, second.usage],
                "records": str(directory),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
