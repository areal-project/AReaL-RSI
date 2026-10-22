"""Authenticated, model-only Responses gateway with explicit sampling and request audits."""

from __future__ import annotations

import argparse
import base64
import http.server
import json
import secrets
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from agent_proxy import ProxyEndpoint

LOCAL_TOOLS = frozenset(
    {
        "exec_command",
        "write_stdin",
        "request_user_input",
        "view_image",
        "apply_patch",
        "get_goal",
        "create_goal",
        "update_goal",
    }
)


@dataclass(frozen=True)
class Sampling:
    model: str
    top_p: float = 0.95
    temperature: float = 0.6
    top_k: int = 20
    max_output_tokens: int = 81920


def configure_request(raw: dict, sampling: Sampling) -> dict:
    """Keep the Responses conversation intact; reject remote tools and set real Qwen knobs."""
    if raw.get("model") != sampling.model:
        raise ValueError("Model differs from the frozen experiment configuration")
    for tool in raw.get("tools", []):
        if tool.get("type") not in {"function", "custom"} or tool.get("name") not in LOCAL_TOOLS:
            raise ValueError(f"Nonlocal tool rejected: {tool.get('type')}/{tool.get('name')}")
    request = dict(raw)
    request.update(asdict(sampling))
    request["chat_template_kwargs"] = {"enable_thinking": True}
    # Qwen3.5's deployed template has no reasoning-effort/budget scale. Preserve the
    # client's requested label for auditing; the effective switch is enable_thinking.
    # No thinking_budget or custom hard cap is introduced.
    return request


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    address = urlsplit(args.base_url)
    if address.scheme != "http" or not address.hostname or address.path.rstrip("/") != "/v1":
        parser.error("--base-url must be an explicit http://host:port/v1 inference endpoint")
    sampling = Sampling(args.model)
    token = secrets.token_urlsafe(32)
    authorization = "Basic " + base64.b64encode(token.encode()).decode()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    lock = threading.Lock()
    args.audit.mkdir(parents=True, exist_ok=False)
    count = 0
    active = 0
    peak = 0

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, *_args: object) -> None:
            pass

        def denied(self, status: int, message: str) -> None:
            body = json.dumps({"error": {"message": message}}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_CONNECT(self) -> None:
            self.denied(403, "External network access is disabled")

        def do_GET(self) -> None:
            if not self.authorized() or self.path != "/v1/models":
                self.denied(403, "Only the configured model API is accessible")
                return
            with opener.open(args.base_url + "/models", timeout=30) as response:
                body = response.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def authorized(self) -> bool:
            supplied = self.headers.get("Proxy-Authorization", "")
            return secrets.compare_digest(supplied, authorization)

        def do_POST(self) -> None:
            nonlocal count, active, peak
            if not self.authorized() or self.path != "/v1/responses":
                self.denied(403, "Only POST /v1/responses is permitted")
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 32 * 1024 * 1024:
                    raise ValueError("Invalid request size")
                original = json.loads(self.rfile.read(size))
                request = configure_request(original, sampling)
            except (ValueError, TypeError) as error:
                self.denied(400, str(error))
                return
            with lock:
                count += 1
                active += 1
                peak = max(peak, active)
                identifier = f"request-{count:06d}"
                maximum = peak
            started = time.monotonic()
            record = args.audit / identifier
            record.mkdir()
            (record / "original.json").write_text(json.dumps(original))
            (record / "effective.json").write_text(json.dumps(request))
            remote = urllib.request.Request(
                args.base_url + "/responses",
                data=json.dumps(request).encode(),
                headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            )
            status = 0
            error_text = None
            try:
                with opener.open(remote, timeout=1200) as response:
                    status = response.status
                    self.send_response(status)
                    self.send_header(
                        "Content-Type", response.headers.get("Content-Type", "text/event-stream")
                    )
                    self.send_header("Connection", "close")
                    self.end_headers()
                    with (record / "response.sse").open("wb") as file:
                        while True:
                            block = response.read1(65536)
                            if not block:
                                break
                            file.write(block)
                            self.wfile.write(block)
                            self.wfile.flush()
            except urllib.error.HTTPError as error:
                status = error.code
                error_text = error.read().decode(errors="replace")
                (record / "error.json").write_text(error_text)
                self.denied(status, error_text)
            except (OSError, TimeoutError) as error:
                error_text = str(error)
            finally:
                with lock:
                    active -= 1
                (record / "receipt.json").write_text(
                    json.dumps(
                        {
                            "status": status,
                            "seconds": time.monotonic() - started,
                            "peak_requests": maximum,
                            "error": error_text,
                        },
                        indent=2,
                    )
                )

    server = http.server.ThreadingHTTPServer((args.listen, 0), Handler)
    endpoint = ProxyEndpoint(args.listen, server.server_port, token)
    with args.output.open("x") as file:
        args.output.chmod(0o600)
        json.dump(asdict(endpoint), file)
    (args.audit / "configuration.json").write_text(
        json.dumps(
            {
                "base_url": args.base_url,
                "sampling": asdict(sampling),
                "thinking": "enable_thinking=true; no separate thinking budget",
                "web_search": False,
                "web_fetch": False,
            },
            indent=2,
        )
    )
    print(f"Model-only gateway ready on {args.listen}:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    finally:
        args.output.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
