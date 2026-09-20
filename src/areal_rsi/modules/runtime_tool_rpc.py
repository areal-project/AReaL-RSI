"""Small, dependency-free client for the current agent's scoped tool service."""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any


async def invoke(config: dict[str, Any], call: dict[str, Any]) -> Any:
    reader, writer = await asyncio.open_connection("127.0.0.1", config["port"], limit=2**20)
    try:
        writer.write((json.dumps({"token": config["token"], **call}) + "\n").encode())
        await writer.drain()
        response = json.loads(await reader.readline())
        if "error" in response:
            raise ValueError(response["error"])
        return response["output"]
    finally:
        writer.close()
        await writer.wait_closed()


if __name__ == "__main__":
    config = json.loads(Path(sys.argv[1]).read_text())
    if sys.argv[2:] == ["list"]:
        print(json.dumps(config["tools"], ensure_ascii=False))
    elif len(sys.argv) in {5, 6} and sys.argv[2] == "call":
        value = asyncio.run(
            invoke(
                config,
                {
                    "name": sys.argv[3],
                    "arguments": json.loads(sys.argv[4]),
                    "call_id": sys.argv[5] if len(sys.argv) == 6 else uuid.uuid4().hex,
                },
            )
        )
        print(json.dumps(value, ensure_ascii=False))
    else:
        raise SystemExit("Expected list or call TOOL JSON_ARGUMENTS [CALL_ID]")
