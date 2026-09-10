"""Read-only, paginated file tools; standalone so runtimes can copy it into a sandbox."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

try:
    from mcp.server.fastmcp import FastMCP  # type: ignore[attr-defined]

    _MCP_V2 = False
except ModuleNotFoundError:
    from mcp.server.mcpserver import MCPServer

    FastMCP = MCPServer
    _MCP_V2 = True

MAX_RESPONSE_BYTES = 6000
MAX_FILE_BYTES = 50_000_000


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


class FileReader:
    """Use an explicit allowlist; expose no execution, mutation or arbitrary JSON queries."""

    def __init__(self, root: Path, paths: list[str]):
        self.root = root.resolve()
        self.paths = frozenset(paths)
        for name in paths:
            self.path(name)

    def path(self, name: str) -> Path:
        relative = Path(name)
        if name not in self.paths or relative.is_absolute() or ".." in relative.parts:
            raise ValueError("File is not granted")
        path = self.root / relative
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise ValueError("Symlink reads are not allowed")
        path.resolve().relative_to(self.root)
        if not path.is_file():
            raise ValueError("Granted file is unavailable")
        return path

    def text(self, path: str) -> str:
        file = self.path(path)
        if file.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("File exceeds the reader size limit")
        return file.read_text(encoding="utf-8")

    @staticmethod
    def page(text: str, offset: int, limit: int) -> str:
        """Return a continuation cursor instead of discarding the beginning or tail."""
        if type(offset) is not int or not 0 <= offset <= len(text) or not 1 <= limit <= 4000:
            raise ValueError("Invalid offset or limit")
        end = min(len(text), offset + limit)
        while True:
            payload = encode(
                {
                    "text": text[offset:end],
                    "offset": offset,
                    "next_offset": end if end < len(text) else None,
                    "total_characters": len(text),
                }
            )
            if len(payload.encode()) <= MAX_RESPONSE_BYTES:
                return payload
            end = offset + (end - offset) // 2

    def value(self, path: str, pointer: str) -> Any:
        """Resolve RFC 6901 pointers without evaluating code or expressions."""
        value = json.loads(self.text(path))
        if pointer and not pointer.startswith("/"):
            raise ValueError("JSON pointer must be empty or start with /")
        for part in pointer.split("/")[1:]:
            key = part.replace("~1", "/").replace("~0", "~")
            value = value[int(key)] if isinstance(value, list) else value[key]
        return value

    def list_files(self, offset: int = 0, limit: int = 10) -> str:
        """List granted names and sizes; use next_offset to continue the file list."""
        names = sorted(self.paths)
        if not 0 <= offset <= len(names) or not 1 <= limit <= 20:
            raise ValueError("Invalid offset or limit")
        end = min(len(names), offset + limit)
        while True:
            result = encode(
                {
                    "files": [
                        {"path": name, "bytes": self.path(name).stat().st_size}
                        for name in names[offset:end]
                    ],
                    "next_offset": end if end < len(names) else None,
                    "total_files": len(names),
                }
            )
            if len(result.encode()) <= MAX_RESPONSE_BYTES:
                return result
            if end <= offset + 1:
                raise ValueError("File name exceeds the response limit")
            end -= 1

    def read_file(self, path: str, offset: int = 0, limit: int = 3000) -> str:
        """Read 1..4000 characters per page. Offsets count characters, not bytes or lines."""
        return self.page(self.text(path), offset, limit)

    def read_json(self, path: str, pointer: str = "", offset: int = 0, limit: int = 3000) -> str:
        """Read a JSON subtree such as /events/3/data. Limit must be 1..4000 characters."""
        return self.page(encode(self.value(path, pointer)), offset, limit)

    def inspect_json(self, path: str, pointer: str = "", offset: int = 0) -> str:
        """Inspect keys or array length without dumping the subtree's contents."""
        value = self.value(path, pointer)
        if isinstance(value, dict):
            return self.page(encode({"type": "object", "keys": list(value)}), offset, 3000)
        if isinstance(value, list):
            return encode({"type": "array", "length": len(value)})
        return self.page(encode({"type": type(value).__name__, "value": value}), offset, 3000)

    def search_file(self, path: str, text: str, offset: int = 0) -> str:
        """Locate the next literal match and a small excerpt; use read_file for more context."""
        source = self.text(path)
        if not text or len(text) > 500 or not 0 <= offset <= len(source):
            raise ValueError("Invalid search text or offset")
        position = source.find(text, offset)
        if position < 0:
            return encode({"match": None, "next_offset": None})
        start = max(0, position - 100)
        return encode(
            {
                "match": position,
                "snippet_offset": start,
                "snippet": source[start : position + len(text) + 200],
                "next_offset": position + len(text),
            }
        )


def create_server(reader: FileReader) -> FastMCP:
    """Each response is bounded; callers explicitly continue using next_offset."""
    server = FastMCP(
        "evidence-files",
        log_level="ERROR",
        instructions=(
            "Read only granted files. Responses are paginated; continue with next_offset. "
            "Use inspect_json for structure, read_json with a JSON pointer for selected fields, "
            "and search_file to locate relevant code or events. "
            "No shell or write tool is available."
        ),
    )
    methods = (
        reader.list_files,
        reader.read_file,
        reader.read_json,
        reader.inspect_json,
        reader.search_file,
    )
    if _MCP_V2:
        for method in methods:
            server.tool(structured_output=False)(method)
    else:
        for method in methods:
            server.add_tool(method, structured_output=False)
    return server


if __name__ == "__main__":
    # The host creates this allowlist before the model starts; the model has no write tool.
    grant = json.loads(Path(sys.argv[1]).read_text())
    create_server(FileReader(Path(grant["root"]), grant["paths"])).run(transport="stdio")
