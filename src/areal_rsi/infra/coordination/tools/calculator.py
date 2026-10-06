"""A bounded exact calculator exposed to Claude Code over MCP stdio."""

from __future__ import annotations

import ast
import json
import sys
from fractions import Fraction
from typing import Any


def calculate(expression: str) -> str:
    if not isinstance(expression, str) or len(expression) > 256:
        raise ValueError("Calculator expression must be at most 256 characters")
    tree = ast.parse(expression, mode="eval")
    nodes = 0

    def visit(node: ast.AST, depth: int) -> Fraction:
        nonlocal nodes
        nodes += 1
        if depth > 32 or nodes > 64:
            raise ValueError("Calculator expression is too complex")
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            if len(str(node.value)) > 40:
                raise ValueError("Calculator literal is too large")
            result = Fraction(str(node.value))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand, depth + 1)
            result = -value if isinstance(node.op, ast.USub) else value
        elif isinstance(node, ast.BinOp) and isinstance(
            node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)
        ):
            left, right = visit(node.left, depth + 1), visit(node.right, depth + 1)
            if isinstance(node.op, ast.Add):
                result = left + right
            elif isinstance(node.op, ast.Sub):
                result = left - right
            elif isinstance(node.op, ast.Mult):
                result = left * right
            else:
                if not right:
                    raise ValueError("Division by zero")
                result = left / right
        else:
            raise ValueError("Only numbers, parentheses, +, -, *, and / are allowed")
        if max(result.numerator.bit_length(), result.denominator.bit_length()) > 4096:
            raise ValueError("Calculator result is too large")
        return result

    return str(visit(tree.body, 0))


def _reply(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    if "id" not in request:
        return None
    identifier = request["id"]
    if method == "initialize":
        params = request.get("params")
        version = (
            params.get("protocolVersion", "2025-06-18")
            if isinstance(params, dict)
            else "2025-06-18"
        )
        result: dict[str, Any] = {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "tir-calculator", "version": "1.0.0"},
        }
    elif method == "tools/list":
        result = {
            "tools": [
                {
                    "name": "calculate",
                    "description": "Exact arithmetic: numbers, parentheses, +, -, *, /.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"expression": {"type": "string"}},
                        "required": ["expression"],
                        "additionalProperties": False,
                    },
                }
            ]
        }
    elif method == "tools/call":
        params = request.get("params")
        args = params.get("arguments") if isinstance(params, dict) else None
        try:
            if (
                not isinstance(params, dict)
                or params.get("name") != "calculate"
                or not isinstance(args, dict)
                or set(args) != {"expression"}
            ):
                raise ValueError("Invalid calculator call")
            value = calculate(args["expression"])
            result = {"content": [{"type": "text", "text": value}]}
        except (SyntaxError, ValueError, ZeroDivisionError) as exc:
            result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    else:
        return {
            "jsonrpc": "2.0",
            "id": identifier,
            "error": {"code": -32601, "message": "Unknown method"},
        }
    return {"jsonrpc": "2.0", "id": identifier, "result": result}


def main() -> None:
    for line in sys.stdin:
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                continue
            reply = _reply(payload)
            if reply is not None:
                sys.stdout.write(json.dumps(reply, separators=(",", ":")) + "\n")
                sys.stdout.flush()
        except json.JSONDecodeError:
            continue


if __name__ == "__main__":
    main()
