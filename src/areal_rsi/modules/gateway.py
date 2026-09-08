"""将 DSH 的 DeepSeek 专有请求字段适配为用户网关支持的 chat 协议。"""

from __future__ import annotations

import json
import threading
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


class GatewayProxy:
    def __init__(self, base_url: str, api_key: str):
        self.requests: list[dict[str, Any]] = []
        owner = self
        upstream_root = base_url.rstrip("/")

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:
                route = self.path.removeprefix("/v1")
                route = "/chat/completions" if route in {"", "/"} else route
                if route not in {"/chat/completions", "/responses", "/messages"}:
                    self.send_error(404)
                    return
                upstream = upstream_root + route
                original = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
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

                            def send_event(data: bytes) -> None:
                                chunk = (
                                    normalize_tool_deltas(data)
                                    if route == "/chat/completions"
                                    else data
                                )
                                self.wfile.write(f"{len(chunk):x}\r\n".encode())
                                self.wfile.write(chunk + b"\r\n")
                                self.wfile.flush()

                            try:
                                while line := response.readline():
                                    raw.extend(line)
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
