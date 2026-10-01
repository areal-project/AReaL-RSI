"""Local Anthropic request adapter for Claude Code and SGLang."""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def adapt_messages(body: bytes, *, max_tokens: int) -> bytes:
    """Move Claude Code's system message to the Anthropic top-level field."""
    payload: dict[str, Any] = json.loads(body)
    payload["max_tokens"] = min(payload.get("max_tokens", max_tokens), max_tokens)
    systems = [
        message["content"]
        for message in payload.get("messages", [])
        if message.get("role") == "system"
    ]
    if systems:
        payload["messages"] = [
            message for message in payload["messages"] if message.get("role") != "system"
        ]
        existing = payload.get("system", "")
        blocks = (
            existing
            if isinstance(existing, list)
            else ([{"type": "text", "text": existing}] if existing else [])
        )
        for system in systems:
            blocks.extend(
                system if isinstance(system, list) else [{"type": "text", "text": str(system)}]
            )
        payload["system"] = blocks
    return json.dumps(payload, ensure_ascii=False).encode()


def serve(*, host: str, port: int, upstream_port: int, max_tokens: int) -> None:
    if host != "127.0.0.1" or not all(1 <= number <= 65535 for number in (port, upstream_port)):
        raise ValueError("Claude Code backend adapter only listens on localhost")
    if max_tokens < 1:
        raise ValueError("max_tokens must be positive")

    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if self.path.split("?", 1)[0] == "/v1/messages":
                body = adapt_messages(body, max_tokens=max_tokens)
            headers = {
                key: value
                for key, value in self.headers.items()
                if key.lower() not in {"host", "content-length", "connection", "accept-encoding"}
            }
            try:
                with urlopen(
                    Request(
                        f"http://127.0.0.1:{upstream_port}{self.path}",
                        data=body,
                        headers=headers,
                        method="POST",
                    ),
                    timeout=300,
                ) as response:
                    result = response.read()
                    status = response.status
                    content_type = response.headers.get("Content-Type", "application/json")
            except HTTPError as error:
                result = error.read()
                status = error.code
                content_type = error.headers.get("Content-Type", "application/json")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(result)))
            self.end_headers()
            self.wfile.write(result)

    ThreadingHTTPServer((host, port), Handler).serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Adapt local Claude Code requests for SGLang")
    parser.add_argument("--port", type=int, default=31931)
    parser.add_argument("--upstream-port", type=int, default=31930)
    parser.add_argument("--max-tokens", type=int, default=4096)
    args = parser.parse_args()
    serve(
        host="127.0.0.1",
        port=args.port,
        upstream_port=args.upstream_port,
        max_tokens=args.max_tokens,
    )


if __name__ == "__main__":
    main()
