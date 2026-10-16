"""Minimal local JSON-RPC MCP arithmetic server; uses the same generic calculator."""

import ast
import json
import sys
from typing import Any

from check import calculate  # type: ignore[import-not-found]

result: dict[str, Any]

for line in sys.stdin:
    try:
        request = json.loads(line)
        method = request.get("method")
        if "id" not in request:
            continue
        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "arithmetic", "version": "1.0"},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": "calculate",
                        "description": "Evaluate arithmetic",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"expression": {"type": "string"}},
                            "required": ["expression"],
                        },
                    }
                ]
            }
        elif method == "tools/call":
            expression = request["params"]["arguments"]["expression"]
            if len(expression) > 1000:
                raise ValueError("Expression too long")
            result = {
                "content": [
                    {
                        "type": "text",
                        "text": str(calculate(ast.parse(expression, mode="eval").body)),
                    }
                ]
            }
        else:
            result = {}
        print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request.get("id"),
                    "error": {"code": -32603, "message": str(exc)},
                }
            ),
            flush=True,
        )
