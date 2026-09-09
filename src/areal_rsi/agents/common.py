"""Agent 共用的 JSON 响应解析和格式重试。"""

from __future__ import annotations

import json
from typing import Any

from areal_rsi.api.modules import AgentSession


def parse_object(text: str) -> dict[str, Any]:
    """接收模型最终 JSON 或 Markdown JSON 块，不修改其中的业务结论。"""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("模型未返回 JSON 对象")
    value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("模型返回值必须为对象")
    return value


async def ask_json(session: AgentSession, prompt: str) -> dict[str, Any]:
    reply = await session.send(prompt)
    try:
        return parse_object(reply)
    except (ValueError, json.JSONDecodeError):
        # 格式重试不补充答案、修改建议或评测数据。
        return parse_object(
            await session.send("请只返回前述要求的合法 JSON 对象，无代码块或额外文本。")
        )
