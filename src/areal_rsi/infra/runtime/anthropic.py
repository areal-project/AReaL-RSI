"""Local Anthropic request adapter for Claude Code and SGLang."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener

from areal_rsi.api.control import json_bytes


def complete_instruction(
    backend_url: str, model: str, prompt: str, *, seed: int, timeout: float
) -> bytes:
    """Sample a base-model continuation from the same local inference service."""
    parsed = urlparse(backend_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Proposal completion requires a local HTTP backend")
    request = Request(
        backend_url.rstrip("/") + "/v1/completions",
        data=json_bytes(
            {
                "model": model,
                "prompt": prompt,
                "max_tokens": 96,
                "temperature": 0.6,
                "seed": seed,
                "stop": ["\n"],
                "stream": False,
            }
        ),
        headers={"Content-Type": "application/json"},
    )
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=timeout) as response:
            raw = response.read(1024 * 1024 + 1)
    except (URLError, TimeoutError) as error:
        raise ValueError("Local proposal completion failed") from error
    if not isinstance(raw, bytes) or len(raw) > 1024 * 1024:
        raise ValueError("Local proposal response exceeds its size limit")
    return raw


def adapt_messages(
    body: bytes, *, max_tokens: int, strip_unsupported_reasoning: bool = False
) -> bytes:
    """Move Claude Code's system message to the Anthropic top-level field."""
    payload: dict[str, Any] = json.loads(body)
    payload["max_tokens"] = min(payload.get("max_tokens", max_tokens), max_tokens)
    if strip_unsupported_reasoning:
        for key in ("thinking", "output_config", "context_management"):
            payload.pop(key, None)
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


def _server(
    *,
    host: str,
    port: int,
    upstream_url: str,
    max_tokens: int,
    strip_unsupported_reasoning: bool = False,
) -> ThreadingHTTPServer:
    upstream = urlparse(upstream_url)
    if (
        host != "127.0.0.1"
        or not 0 <= port <= 65535
        or upstream.scheme not in {"http", "https"}
        or not upstream.hostname
        or upstream.username
        or upstream.password
        or upstream.path not in {"", "/"}
        or upstream.query
        or upstream.fragment
    ):
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
                body = adapt_messages(
                    body,
                    max_tokens=max_tokens,
                    strip_unsupported_reasoning=strip_unsupported_reasoning,
                )
            headers = {
                key: value
                for key, value in self.headers.items()
                if key.lower() not in {"host", "content-length", "connection", "accept-encoding"}
            }
            try:
                with build_opener(ProxyHandler({})).open(
                    Request(
                        f"{upstream_url.rstrip('/')}{self.path}",
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

    return ThreadingHTTPServer((host, port), Handler)


@contextmanager
def local_adapter(upstream_url: str, *, max_tokens: int) -> Iterator[str]:
    """Adapt one Claude Code rollout, including its session-scoped AReaL proxy."""
    server = _server(
        host="127.0.0.1",
        port=0,
        upstream_url=upstream_url,
        max_tokens=max_tokens,
        strip_unsupported_reasoning=True,
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def serve(
    *,
    host: str,
    port: int,
    upstream_port: int,
    max_tokens: int,
    strip_unsupported_reasoning: bool = False,
) -> None:
    if not 1 <= upstream_port <= 65535:
        raise ValueError("Invalid local upstream port")
    with _server(
        host=host,
        port=port,
        upstream_url=f"http://127.0.0.1:{upstream_port}",
        max_tokens=max_tokens,
        strip_unsupported_reasoning=strip_unsupported_reasoning,
    ) as server:
        server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Adapt local Claude Code requests for SGLang")
    parser.add_argument("--port", type=int, default=31931)
    parser.add_argument("--upstream-port", type=int, default=31930)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--strip-unsupported-reasoning", action="store_true")
    args = parser.parse_args()
    serve(
        host="127.0.0.1",
        port=args.port,
        upstream_port=args.upstream_port,
        max_tokens=args.max_tokens,
        strip_unsupported_reasoning=args.strip_unsupported_reasoning,
    )


if __name__ == "__main__":
    main()
