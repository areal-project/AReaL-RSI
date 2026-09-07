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
        upstream = base_url.rstrip("/") + "/chat/completions"

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                original = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                # 兼容标准 chat 服务，移除 DSH 专有字段。
                # 不改变 model/messages/tools、token 上限或模型返回内容。
                removed = ("thinking", "reasoning_effort", "dsh_plugin_packages", "dsh_session_log")
                forwarded = {k: v for k, v in original.items() if k not in removed}
                payload = json.dumps(forwarded).encode()
                headers = {"Content-Type": "application/json"}
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
                try:
                    with urllib.request.urlopen(request, timeout=210) as response:
                        body = response.read()
                        entry["content_type"] = response.headers.get("Content-Type")
                        entry["status"] = response.status
                        entry["response"] = body.decode(errors="replace")
                        if "text/event-stream" in response.headers.get("Content-Type", ""):
                            body = normalize_tool_deltas(body)
                        self.send_response(response.status)
                        self.send_header(
                            "Content-Type", response.headers.get("Content-Type", "text/plain")
                        )
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                except Exception as exc:
                    entry["error"] = str(exc)
                    self.send_error(502, "upstream request failed")

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
