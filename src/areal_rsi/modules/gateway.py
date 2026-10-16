"""将 DSH 的 DeepSeek 专有请求字段适配为用户网关支持的 chat 协议。"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def normalize_tool_deltas(body: bytes) -> bytes:
    """null 表示增量未更新，不能覆盖第一片中的真实工具 ID/名称。"""
    lines = []
    for line in body.decode().splitlines():
        if line.startswith("data: {"):
            item = json.loads(line[6:])
            for choice in item.get("choices", []):
                for call in choice.get("delta", {}).get("tool_calls") or []:
                    if call.get("id", 1) is None:
                        del call["id"]
                    function = call.get("function") or {}
                    if function.get("name", 1) is None:
                        del function["name"]
            line = "data: " + json.dumps(item, ensure_ascii=False)
        lines.append(line)
    return ("\n".join(lines) + "\n\n").encode()


class ChatStreamBatcher:
    """Coalesce adjacent string deltas without changing text or tool-call boundaries.

    Runtime 0.3 rewrites its canonical log for every event. Token-sized chunks make
    long tool arguments quadratic to persist. Keep the first event immediate and
    retain the unmodified upstream stream separately in the proxy audit.
    """

    def __init__(self, *, max_chars: int = 2048, interval: float = 1.0):
        self.max_chars, self.interval = max_chars, interval
        self.pending: dict[str, Any] | None = None
        self.slot: dict[str, Any] = {}
        self.field = self.key = ""
        self.last_flush = time.monotonic()
        self.sent = False

    def flush(self) -> list[bytes]:
        if self.pending is None:
            return []
        data = ("data: " + json.dumps(self.pending, ensure_ascii=False) + "\n\n").encode()
        self.pending = None
        self.last_flush = time.monotonic()
        self.sent = True
        return [data]

    def push(self, data: bytes) -> list[bytes]:
        normalized = normalize_tool_deltas(data)
        lines = normalized.decode().strip().splitlines()
        candidate: dict[str, Any] | None = None
        slot: dict[str, Any] = {}
        field = ""
        if len(lines) == 1 and lines[0].startswith("data: {"):
            item = json.loads(lines[0][6:])
            choices = item.get("choices", [])
            if len(choices) == 1 and choices[0].get("finish_reason") is None:
                delta = choices[0].get("delta", {})
                if len(delta) == 1:
                    for name in ("content", "reasoning_content"):
                        if isinstance(delta.get(name), str):
                            slot, field, candidate = delta, name, item
                    calls = delta.get("tool_calls")
                    if isinstance(calls, list) and len(calls) == 1:
                        call = calls[0]
                        function = call.get("function", {})
                        if (
                            set(call) <= {"index", "function"}
                            and set(function) == {"arguments"}
                            and isinstance(function["arguments"], str)
                        ):
                            slot, field, candidate = function, "arguments", item
        if candidate is None or not self.sent:
            result = self.flush()
            self.sent = True
            return [*result, normalized]
        value = slot[field]
        slot[field] = ""
        key = json.dumps(candidate, sort_keys=True, ensure_ascii=False)
        slot[field] = value
        result = [] if key == self.key else self.flush()
        if self.pending is None:
            self.pending, self.slot, self.field, self.key = candidate, slot, field, key
        else:
            self.slot[self.field] += value
        if (
            len(self.slot[self.field]) >= self.max_chars
            or time.monotonic() - self.last_flush >= self.interval
        ):
            result.extend(self.flush())
        return result


class GatewayProxy:
    def __init__(self, base_url: str, api_key: str, *, model: str | None = None):
        self.requests: list[dict[str, Any]] = []
        owner = self
        upstream_root = base_url.rstrip("/")

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            disable_nagle_algorithm = True

            def do_POST(self) -> None:
                route = self.path.removeprefix("/v1")
                route = "/chat/completions" if route in {"", "/"} else route
                if route not in {"/chat/completions", "/responses", "/messages"}:
                    self.send_error(404)
                    return
                upstream = upstream_root + route
                original = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if model is not None and original.get("model") != model:
                    self.send_error(403, "model is fixed by the confirmed deployment")
                    return
                # 兼容标准 chat 服务，移除 DSH 专有字段。
                # 不改变 model/messages/tools、token 上限或模型返回内容。
                removed: tuple[str, ...] = (
                    "thinking",
                    "reasoning_effort",
                    "dsh_plugin_packages",
                    "dsh_session_log",
                )
                if route != "/chat/completions":
                    removed = ()
                forwarded = {k: v for k, v in original.items() if k not in removed}
                if route != "/messages":
                    forwarded["store"] = False
                payload = json.dumps(forwarded).encode()
                headers = {"Content-Type": "application/json"}
                if route == "/messages":
                    headers["anthropic-version"] = self.headers.get(
                        "anthropic-version", "2023-06-01"
                    )
                    if self.headers.get("anthropic-beta"):
                        headers["anthropic-beta"] = self.headers["anthropic-beta"]
                    if api_key:
                        headers["x-api-key"] = api_key
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                request = urllib.request.Request(
                    upstream,
                    data=payload,
                    headers=headers,
                )
                entry: dict[str, Any] = {
                    "request": forwarded,
                    "removed_fields": [k for k in removed if k in original],
                }
                owner.requests.append(entry)
                headers_sent = False
                try:
                    with urllib.request.urlopen(request, timeout=210) as response:
                        entry["content_type"] = response.headers.get("Content-Type")
                        entry["status"] = response.status
                        content_type = response.headers.get("Content-Type", "text/plain")
                        if "text/event-stream" in content_type:
                            self.send_response(response.status)
                            self.send_header("Content-Type", content_type)
                            # 流长度未知，用 chunked 明确结束边界，不依赖套接字 EOF。
                            self.send_header("Transfer-Encoding", "chunked")
                            self.send_header("Connection", "close")
                            self.end_headers()
                            self.close_connection = headers_sent = True
                            raw, event = bytearray(), bytearray()
                            batcher = ChatStreamBatcher() if route == "/chat/completions" else None

                            def send_event(data: bytes) -> None:
                                for chunk in batcher.push(data) if batcher else [data]:
                                    write_chunk(chunk)

                            def write_chunk(chunk: bytes) -> None:
                                self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                                self.wfile.flush()

                            try:
                                while line := response.readline():
                                    raw.extend(line)
                                    entry["received_bytes"] = len(raw)
                                    entry["last_received_at"] = time.monotonic()
                                    event.extend(line)
                                    if line in (b"\n", b"\r\n"):
                                        completed_event = bytes(event)
                                        send_event(completed_event)
                                        event.clear()
                                        # chat 协议在 DONE 处完成，不再等待上游关闭长连接。
                                        if any(
                                            part.startswith(b"data:")
                                            and part[5:].strip() == b"[DONE]"
                                            for part in completed_event.splitlines()
                                        ):
                                            break
                                if event:
                                    send_event(bytes(event))
                                if batcher:
                                    for chunk in batcher.flush():
                                        write_chunk(chunk)
                                self.wfile.write(b"0\r\n\r\n")
                                self.wfile.flush()
                            finally:
                                # 审计保留未归一化的上游原文，包括断连前已收到的完整/部分事件。
                                entry["response"] = raw.decode(errors="replace")
                            return
                        body = response.read()
                        entry["response"] = body.decode(errors="replace")
                        self.send_response(response.status)
                        self.send_header("Content-Type", content_type)
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        headers_sent = True
                        self.wfile.write(body)
                except Exception as exc:
                    entry["error"] = str(exc)
                    if not headers_sent:
                        self.send_error(502, "upstream request failed")
                    else:
                        # 已开始的 SSE 不能再混入第二个 HTTP 响应，断连也应关闭上游连接。
                        self.close_connection = True

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
