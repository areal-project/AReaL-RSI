"""Candidate file tools for Claude Code; execution is owned by the evaluator."""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any

from areal_rsi.api.control import WorkspaceOperation, WorkspaceRequest, decode, json_bytes
from areal_rsi.infra.storage.files import write_bytes


def candidate_path(root: Path, relative: str) -> Path:
    if Path(relative).is_absolute() or root.is_symlink():
        raise ValueError("Workspace paths must be relative to the assigned candidate")
    root = root.resolve(strict=True)
    path = root / relative
    if not path.resolve().is_relative_to(root):
        raise ValueError("Workspace path escapes candidate")
    # Reject even an internal symlink: copying or replacing it changes the identity
    # of a submission and the bench's snapshot contract excludes all symlinks.
    if any(parent.is_symlink() for parent in (path, *path.parents) if parent != root):
        raise ValueError("Workspace path contains a symlink")
    return path


def perform(root: Path, request: WorkspaceRequest) -> str:
    path = candidate_path(root, request.path)
    if request.operation is WorkspaceOperation.LIST:
        if not path.is_dir():
            raise ValueError("List requires a candidate directory")
        files = sorted(
            file.relative_to(root).as_posix()
            for file in path.rglob("*")
            if file.is_file()
            and not file.is_symlink()
            and not any(part in {".git", "__pycache__"} for part in file.parts)
        )
        return "\n".join(files[:2000])
    if request.operation is WorkspaceOperation.WRITE:
        write_bytes(path, request.content.encode())
        return f"Wrote {request.path}"
    if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Candidate file must be regular and at most 2 MiB")
    content = path.read_text()
    if request.operation is WorkspaceOperation.READ:
        first = request.start_line - 1
        lines = content.splitlines()[first : first + request.max_lines]
        return "\n".join(f"{index}: {line}" for index, line in enumerate(lines, first + 1))[:32000]
    if request.operation is WorkspaceOperation.REPLACE:
        if content.count(request.search) != 1:
            raise ValueError("Replacement search must match exactly once")
        write_bytes(path, content.replace(request.search, request.content, 1).encode())
        return f"Replaced text in {request.path}"
    if path.suffix != ".py":
        raise ValueError("Syntax check requires a Python source file")
    ast.parse(content, filename=request.path)
    return f"Python syntax valid: {request.path}"


def reply(root: Path, payload: dict[str, Any]) -> dict[str, Any] | None:
    if "id" not in payload:
        return None
    method = payload.get("method")
    if method == "initialize":
        params = payload.get("params", {})
        result: dict[str, Any] = {
            "protocolVersion": params.get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "rsi-workspace", "version": "1.0.0"},
        }
    elif method == "tools/list":
        result = {
            "tools": [
                {
                    "name": "workspace",
                    "description": (
                        "List, read, write, replace exact text, or check Python syntax inside "
                        "your candidate source tree. GPU execution and grading are independent."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "operation": {
                                "type": "string",
                                "enum": [operation.value for operation in WorkspaceOperation],
                            },
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                            "search": {"type": "string"},
                            "start_line": {"type": "integer", "minimum": 1},
                            "max_lines": {"type": "integer", "minimum": 1, "maximum": 500},
                        },
                        "required": ["operation", "path"],
                        "additionalProperties": False,
                    },
                }
            ]
        }
    elif method == "tools/call":
        try:
            params = payload["params"]
            if params["name"] != "workspace" or not isinstance(params["arguments"], dict):
                raise ValueError("Invalid workspace tool call")
            arguments = {"content": "", "search": "", "start_line": 1, "max_lines": 200}
            arguments.update(params["arguments"])
            request = decode(WorkspaceRequest, arguments)
            value = perform(root, request)
            result = {"content": [{"type": "text", "text": value}]}
        except (KeyError, OSError, ValueError, SyntaxError) as error:
            result = {"content": [{"type": "text", "text": str(error)}], "isError": True}
    else:
        return {
            "jsonrpc": "2.0",
            "id": payload["id"],
            "error": {"code": -32601, "message": "Unknown method"},
        }
    return {"jsonrpc": "2.0", "id": payload["id"], "result": result}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = candidate_path(args.root, "")
    for line in sys.stdin:
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                continue
            response = reply(root, payload)
            if response is not None:
                sys.stdout.buffer.write(json_bytes(response) + b"\n")
                sys.stdout.buffer.flush()
        except json.JSONDecodeError:
            continue


if __name__ == "__main__":
    main()
