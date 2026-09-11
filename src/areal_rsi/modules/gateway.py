"""将 DSH 的 DeepSeek 专有请求字段适配为用户网关支持的 chat 协议。"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

_MAX_STREAM_ATTEMPTS = 6
_MAX_RETRY_DELAY_SECONDS = 8.0


def _event_payload(event: bytes) -> dict[str, Any] | None:
    """读取单个 SSE 事件的 JSON data；注释、DONE 和非 JSON 事件返回 None。"""
    for line in event.splitlines():
        if not line.startswith(b"data:"):
            continue
        data = line[5:].strip()
        if not data or data == b"[DONE]":
            continue
        try:
            payload = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        return payload if isinstance(payload, dict) else None
    return None


def _retryable_stream_error(event: bytes) -> bool:
    """只识别服务端明确声明的瞬时过载，认证和请求错误不能通过重试掩盖。"""
    payload = _event_payload(event)
    error = payload.get("error") if payload else None
    if not isinstance(error, dict):
        return False
    message = str(error.get("message", "")).lower()
    kind = str(error.get("type", "")).lower()
    return kind in {"upstream_error", "server_error"} and any(
        marker in message for marker in ("overload", "try again", "temporar")
    )


def _has_meaningful_delta(event: bytes) -> bool:
    """正文、工具调用或 finish_reason 都意味着重放可能造成重复副作用。"""
    payload = _event_payload(event)
    if not payload:
        return False
    for choice in payload.get("choices") or []:
        if choice.get("finish_reason") is not None:
            return True
        delta = choice.get("delta") or {}
        if any(value not in (None, "", [], {}) for key, value in delta.items() if key != "role"):
            return True
    return False


def _done(event: bytes, route: str) -> bool:
    payload = _event_payload(event) or {}
    if payload.get("error") or payload.get("type") == "error":
        return True
    if route == "/responses":
        return payload.get("type") in {
            "response.completed", "response.failed", "response.incomplete"
        }
    if route == "/messages":
        return payload.get("type") == "message_stop"
    return any(
        part.startswith(b"data:") and part[5:].strip() == b"[DONE]" for part in event.splitlines()
    )


def _relay_stream(
    response: Any, send_event: Callable[[bytes], None], *, route: str, raw: bytearray
) -> tuple[bool, str | None]:
    """逐行记录原文，返回完成状态及尚未产生可见输出时的恢复原因。"""
    event = bytearray()
    # role-only 首事件暂存到真正输出出现；紧随其后的过载才可以透明重试。
    pending: list[bytes] = []
    meaningful = False
    while line := response.readline():
        raw.extend(line)
        event.extend(line)
        if line not in (b"\n", b"\r\n"):
            continue
        completed_event = bytes(event)
        event.clear()
        if (
            route == "/chat/completions"
            and _retryable_stream_error(completed_event)
            and not meaningful
        ):
            return False, "overload"
        if route != "/chat/completions" or _has_meaningful_delta(completed_event):
            for buffered in pending:
                send_event(buffered)
            pending.clear()
            meaningful = True
            send_event(completed_event)
        elif meaningful:
            send_event(completed_event)
        else:
            pending.append(completed_event)
        if _done(completed_event, route):
            for buffered in pending:
                send_event(buffered)
            return True, None
    if event:
        # 不完整事件已经来自实际响应，不能假定重放请求不会造成重复输出。
        send_event(bytes(event))
        return False, None
    if route == "/chat/completions" and not meaningful:
        # 空流或只有 role/comment 的流没有对调用方产生可见输出，可安全改走完整响应。
        return False, "empty"
    for buffered in pending:
        send_event(buffered)
    return False, None


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


def _completion_to_sse(body: bytes) -> bytes:
    """Convert one complete Chat Completions response into a terminal SSE event."""
    try:
        completion = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("non-stream fallback returned invalid JSON") from exc
    choices = completion.get("choices") if isinstance(completion, dict) else None
    if not isinstance(choices, list) or not choices:
        raise ValueError("non-stream fallback returned no choices")
    chunks = []
    for index, choice in enumerate(choices):
        if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
            raise ValueError("non-stream fallback returned an invalid choice")
        finish_reason = choice.get("finish_reason")
        if not isinstance(finish_reason, str) or not finish_reason:
            raise ValueError("non-stream fallback did not finish")
        chunks.append(
            {
                "index": choice.get("index", index),
                "delta": choice["message"],
                "finish_reason": finish_reason,
                "logprobs": choice.get("logprobs"),
            }
        )
    event = {
        key: completion[key]
        for key in ("id", "created", "model", "system_fingerprint", "usage")
        if key in completion
    }
    event.update(object="chat.completion.chunk", choices=chunks)
    return b"data: " + json.dumps(event, ensure_ascii=False).encode() + b"\n\ndata: [DONE]\n\n"


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

                def fallback_completion() -> bytes:
                    fallback_payload = dict(forwarded)
                    fallback_payload["stream"] = False
                    fallback_payload.pop("stream_options", None)
                    fallback_request = urllib.request.Request(
                        upstream,
                        data=json.dumps(fallback_payload).encode(),
                        headers=headers,
                    )
                    with urllib.request.urlopen(fallback_request, timeout=210) as fallback:
                        fallback_body = fallback.read()
                        entry["fallback_status"] = fallback.status
                        entry["fallback_content_type"] = fallback.headers.get("Content-Type")
                    entry["fallback_response"] = fallback_body.decode(errors="replace")
                    entry["stream_fallback"] = True
                    return _completion_to_sse(fallback_body)

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

                            def send_event(data: bytes) -> None:
                                chunk = (
                                    normalize_tool_deltas(data)
                                    if route == "/chat/completions"
                                    else data
                                )
                                self.wfile.write(f"{len(chunk):x}\r\n".encode())
                                self.wfile.write(chunk + b"\r\n")
                                self.wfile.flush()

                            raw = bytearray()
                            try:
                                _, retry_reason = _relay_stream(
                                    response, send_event, route=route, raw=raw
                                )
                                # 已有正文或工具调用时绝不重放请求，避免重复输出或副作用。
                                for attempt in range(1, _MAX_STREAM_ATTEMPTS):
                                    if retry_reason != "overload":
                                        break
                                    # 号池过载通常会持续数秒；仅在尚未输出正文/工具调用时
                                    # 使用封顶指数退避，避免重放已经可见或可能有副作用的内容。
                                    delay = min(2 ** (attempt - 1), _MAX_RETRY_DELAY_SECONDS)
                                    time.sleep(delay)
                                    with urllib.request.urlopen(request, timeout=210) as retried:
                                        _, retry_reason = _relay_stream(
                                            retried, send_event, route=route, raw=raw
                                        )
                                    entry["stream_retries"] = attempt
                                if retry_reason and route == "/chat/completions":
                                    # Some compatible gateways can serve a complete response while
                                    # their SSE pool is overloaded. No meaningful delta has reached
                                    # the caller, so one non-stream request cannot duplicate visible
                                    # output or tool side effects.
                                    send_event(fallback_completion())
                                    retry_reason = None
                                if retry_reason:
                                    send_event(
                                        b'data: {"error":{"message":"upstream service remained '
                                        b'overloaded after retries","type":"upstream_error"}}\n\n'
                                    )
                                self.wfile.write(b"0\r\n\r\n")
                                self.wfile.flush()
                            finally:
                                # 审计保留未归一化的上游原文，包括断连前已收到的完整/部分事件。
                                entry["response"] = raw.decode(errors="replace")
                            return
                        body = response.read()
                        entry["response"] = body.decode(errors="replace")
                        if route == "/chat/completions" and original.get("stream") is True:
                            try:
                                body = _completion_to_sse(body)
                            except ValueError:
                                event = b"data: " + body + b"\n\n"
                                if not _retryable_stream_error(event):
                                    raise
                                body = fallback_completion()
                            content_type = "text/event-stream"
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
