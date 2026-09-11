import json
import threading
import urllib.request
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO

import pytest

from areal_rsi.modules.gateway import GatewayProxy, _relay_stream, normalize_tool_deltas


@pytest.fixture(autouse=True)
def bypass_local_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1")


@pytest.mark.parametrize(
    ("route", "first", "last"),
    [
        (
            "/responses",
            b'event: response.output_text.delta\ndata: '
            b'{"type":"response.output_text.delta","delta":"hello"}\n\n',
            b'event: response.completed\ndata: {"type":"response.completed"}\n\n',
        ),
        (
            "/messages",
            b'event: content_block_delta\ndata: {"type":"content_block_delta",'
            b'"delta":{"type":"text_delta","text":"hello"}}\n\n',
            b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ),
    ],
)
def test_other_protocols_stream_immediately_and_finish_without_eof(
    route: str, first: bytes, last: bytes
) -> None:
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers["Content-Length"]))
            assert self.path == route
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(first)
            self.wfile.flush()
            if release.wait(timeout=3):
                self.wfile.write(last)
                self.wfile.flush()
                # Keep the connection open until the client has seen completion.
                finished.wait(timeout=3)

        def log_message(self, format: str, *args: object) -> None:
            pass

    finished = threading.Event()
    with local_upstream(Handler) as upstream:
        proxy = GatewayProxy(upstream, "offline-fixture")
        try:
            request = urllib.request.Request(
                proxy.url + route,
                data=b'{"model":"fixture","stream":true}',
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=2) as response:
                assert response.read(len(first)) == first
                release.set()
                assert response.read() == last
            assert proxy.requests[0]["response"] == (first + last).decode()
        finally:
            release.set()
            finished.set()
            proxy.close()


@pytest.mark.parametrize("failure", ["upstream", "downstream"])
def test_stream_audit_survives_io_failure(failure: str) -> None:
    event = b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
    partial = b'data: {"choices":'

    class InterruptedStream(BytesIO):
        def readline(self, size: int | None = -1) -> bytes:
            line = super().readline(size)
            if not line:
                raise TimeoutError("upstream stalled")
            return line

    sent = []

    def send(data: bytes) -> None:
        if failure == "downstream":
            raise BrokenPipeError("client disconnected")
        sent.append(data)

    raw = bytearray()
    with pytest.raises((TimeoutError, BrokenPipeError)):
        _relay_stream(
            InterruptedStream(event + partial), send, route="/chat/completions", raw=raw
        )
    assert raw == (event + partial if failure == "upstream" else event)
    assert sent == ([event] if failure == "upstream" else [])


def test_non_retryable_error_is_forwarded_and_role_only_eof_can_fallback() -> None:
    role = b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
    error = b'data: {"error":{"type":"invalid_request_error","message":"bad request"}}\n\n'
    sent: list[bytes] = []
    raw = bytearray()
    assert _relay_stream(
        BytesIO(role), sent.append, route="/chat/completions", raw=raw
    ) == (False, "empty")
    assert sent == [] and raw == role

    sent = []
    raw = bytearray()
    assert _relay_stream(
        BytesIO(role + error), sent.append, route="/chat/completions", raw=raw
    ) == (True, None)
    assert b"".join(sent) == role + error
    assert raw == role + error


@contextmanager
def local_upstream(handler: type[BaseHTTPRequestHandler]) -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_null_deltas_do_not_erase_tool_name_or_change_arguments() -> None:
    events = [
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "index": 0,
                                "function": {"name": "Bash", "arguments": ""},
                            }
                        ]
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "id": None,
                                "index": 0,
                                "function": {"name": None, "arguments": '{"command":"pwd"}'},
                            }
                        ]
                    }
                }
            ]
        },
    ]
    raw = "\n\n".join("data: " + json.dumps(event) for event in events) + "\n\ndata: [DONE]\n\n"
    result = normalize_tool_deltas(raw.encode()).decode()
    chunks = [json.loads(line[6:]) for line in result.splitlines() if line.startswith("data: {")]
    first = chunks[0]["choices"][0]["delta"]["tool_calls"][0]
    second = chunks[1]["choices"][0]["delta"]["tool_calls"][0]
    assert first["function"]["name"] == "Bash"
    assert "name" not in second["function"] and "id" not in second
    assert second["function"]["arguments"] == '{"command":"pwd"}'
    assert "[DONE]" in result


def test_sse_first_event_arrives_before_upstream_finishes_with_split_utf8() -> None:
    # 只有测试收到完整首事件后，上游才允许继续；整包缓存实现会在此超时失败。
    release = threading.Event()
    first_received = threading.Event()
    upstream_finished = threading.Event()
    events = [
        {
            "choices": [
                {
                    "delta": {
                        "content": "开始",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "function": {"name": "Bash", "arguments": '{"文本":'},
                            }
                        ],
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "content": "完成",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": None,
                                "function": {"name": None, "arguments": '"你好"}'},
                            }
                        ],
                    }
                }
            ]
        },
        {"choices": [], "usage": {"prompt_tokens": 2, "completion_tokens": 3}},
    ]
    first = ("data: " + json.dumps(events[0], ensure_ascii=False) + "\r\n\r\n").encode()
    rest = (
        "".join("data: " + json.dumps(event, ensure_ascii=False) + "\n\n" for event in events[1:])
        + "data: [DONE]\n\n"
    ).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.end_headers()
            # 单字节分片刻意拆开 UTF-8 中文及 CRLF，代理必须等事件完整再解码。
            for byte in first:
                self.wfile.write(bytes([byte]))
                self.wfile.flush()
            release.wait(timeout=3)
            for index in range(0, len(rest), 5):
                self.wfile.write(rest[index : index + 5])
                self.wfile.flush()
            upstream_finished.set()

        def log_message(self, format: str, *args: object) -> None:
            pass

    with local_upstream(Handler) as upstream:
        proxy = GatewayProxy(upstream, "offline-fixture")
        try:

            def consume() -> bytes:
                request = urllib.request.Request(
                    proxy.url,
                    data=b'{"model":"gateway-fixture-model","messages":[],"stream":true}',
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    assert response.headers.get("Content-Length") is None
                    raw = bytearray()
                    seen_data = False
                    while line := response.readline():
                        raw.extend(line)
                        seen_data = seen_data or line.startswith(b"data: {")
                        if seen_data and line == b"\n":
                            first_received.set()
                    return bytes(raw)

            with ThreadPoolExecutor(max_workers=1) as pool:
                received = pool.submit(consume)
                try:
                    assert first_received.wait(timeout=2), "首事件被缓存到上游结束"
                    assert not upstream_finished.is_set()
                finally:
                    release.set()
                body = received.result(timeout=5)
            chunks = [
                json.loads(line[6:])
                for line in body.decode().splitlines()
                if line.startswith("data: {")
            ]
            first_call = chunks[0]["choices"][0]["delta"]["tool_calls"][0]
            later_call = chunks[1]["choices"][0]["delta"]["tool_calls"][0]
            assert first_call["id"] == "call_1" and first_call["function"]["name"] == "Bash"
            assert "id" not in later_call and "name" not in later_call["function"]
            assert first_call["function"]["arguments"] + later_call["function"]["arguments"] == (
                '{"文本":"你好"}'
            )
            assert [chunk["choices"][0]["delta"]["content"] for chunk in chunks[:2]] == [
                "开始",
                "完成",
            ]
            assert chunks[2] == events[2] and b"data: [DONE]" in body
            assert proxy.requests[0]["response"] == (first + rest).decode()
            assert proxy.requests[0]["status"] == 200
            assert "error" not in proxy.requests[0]
        finally:
            release.set()
            proxy.close()


def test_stream_retries_overload_before_meaningful_output(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0
    role = b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
    overloaded = (
        b'data: {"error":{"message":"Our servers are currently overloaded. '
        b'Please try again later.","type":"upstream_error"}}\n\n'
    )
    completed = (
        role
        + b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}\n\n'
        + b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        + b"data: [DONE]\n\n"
    )

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            nonlocal attempts
            attempts += 1
            self.rfile.read(int(self.headers["Content-Length"]))
            body = role + overloaded if attempts < 6 else completed
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()

        def log_message(self, format: str, *args: object) -> None:
            pass

    monkeypatch.setattr("areal_rsi.modules.gateway.time.sleep", lambda _: None)
    with local_upstream(Handler) as upstream:
        proxy = GatewayProxy(upstream, "offline-fixture")
        try:
            request = urllib.request.Request(
                proxy.url,
                data=b'{"model":"gateway-fixture-model","messages":[],"stream":true}',
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                body = response.read()
            assert attempts == 6
            events = body.decode().splitlines()
            chunks = [json.loads(line[6:]) for line in events if line.startswith("data: {")]
            assert (
                sum(
                    choice.get("delta", {}).get("role") == "assistant"
                    for chunk in chunks
                    for choice in chunk.get("choices", [])
                )
                == 1
            )
            assert chunks[1]["choices"][0]["delta"]["content"] == "ok"
            assert "data: [DONE]" in events
            assert not any("error" in chunk for chunk in chunks)
            assert proxy.requests[0]["stream_retries"] == 5
            assert overloaded.decode() in proxy.requests[0]["response"]
        finally:
            proxy.close()


def test_stream_overload_falls_back_to_complete_chat_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, object]] = []
    overloaded = (
        b'data: {"error":{"message":"Our servers are currently overloaded. '
        b'Please try again later.","type":"upstream_error"}}\n\n'
    )
    completion = {
        "id": "chatcmpl-fixture",
        "object": "chat.completion",
        "created": 1,
        "model": "gateway-fixture-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "recovered"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
    }

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            self.send_response(200)
            if request["stream"]:
                body = overloaded
                self.send_header("Content-Type", "text/event-stream")
            else:
                body = json.dumps(completion).encode()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()

        def log_message(self, format: str, *args: object) -> None:
            pass

    monkeypatch.setattr("areal_rsi.modules.gateway.time.sleep", lambda _: None)
    with local_upstream(Handler) as upstream:
        proxy = GatewayProxy(upstream, "offline-fixture")
        try:
            request = urllib.request.Request(
                proxy.url,
                data=json.dumps(
                    {
                        "model": "gateway-fixture-model",
                        "messages": [],
                        "stream": True,
                        "stream_options": {"include_usage": True},
                    }
                ).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                body = response.read()
            chunks = [
                json.loads(line[6:])
                for line in body.decode().splitlines()
                if line.startswith("data: {")
            ]
            assert len(requests) == 7
            assert all(item["stream"] is True for item in requests[:6])
            assert requests[-1]["stream"] is False
            assert "stream_options" not in requests[-1]
            assert chunks == [
                {
                    "id": "chatcmpl-fixture",
                    "created": 1,
                    "model": "gateway-fixture-model",
                    "usage": completion["usage"],
                    "object": "chat.completion.chunk",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": "recovered"},
                            "finish_reason": "stop",
                            "logprobs": None,
                        }
                    ],
                }
            ]
            assert b"data: [DONE]" in body and b"upstream_error" not in body
            assert proxy.requests[0]["stream_retries"] == 5
            assert proxy.requests[0]["stream_fallback"] is True
            assert proxy.requests[0]["fallback_status"] == 200
        finally:
            proxy.close()


def test_complete_json_response_is_converted_when_stream_was_requested() -> None:
    completion = {
        "id": "chatcmpl-json-fixture",
        "model": "gateway-fixture-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "json recovery"},
                "finish_reason": "stop",
            }
        ],
    }
    received: list[dict[str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps(completion).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    with local_upstream(Handler) as upstream:
        proxy = GatewayProxy(upstream, "offline-fixture")
        try:
            request = urllib.request.Request(
                proxy.url,
                data=json.dumps(
                    {"model": "gateway-fixture-model", "messages": [], "stream": True}
                ).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                body = response.read()
                assert response.headers["Content-Type"] == "text/event-stream"
            chunks = [
                json.loads(line[6:])
                for line in body.decode().splitlines()
                if line.startswith("data: {")
            ]
            assert received == [
                {
                    "model": "gateway-fixture-model",
                    "messages": [],
                    "stream": True,
                    "store": False,
                }
            ]
            assert chunks[0]["choices"][0]["delta"]["content"] == "json recovery"
            assert b"data: [DONE]" in body
            assert "stream_fallback" not in proxy.requests[0]
        finally:
            proxy.close()


def test_empty_stream_uses_immediate_non_stream_fallback() -> None:
    requests: list[dict[str, object]] = []
    completion = {
        "id": "chatcmpl-empty-fixture",
        "model": "gateway-fixture-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "recovered from empty"},
                "finish_reason": "stop",
            }
        ],
    }

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            self.send_response(200)
            if request["stream"]:
                body = b""
                self.send_header("Content-Type", "text/event-stream")
            else:
                body = json.dumps(completion).encode()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    with local_upstream(Handler) as upstream:
        proxy = GatewayProxy(upstream, "offline-fixture")
        try:
            request = urllib.request.Request(
                proxy.url,
                data=b'{"model":"gateway-fixture-model","messages":[],"stream":true}',
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                body = response.read()
            assert len(requests) == 2
            assert requests[0]["stream"] is True and requests[1]["stream"] is False
            assert b"recovered from empty" in body and b"data: [DONE]" in body
            assert proxy.requests[0]["stream_fallback"] is True
            assert "stream_retries" not in proxy.requests[0]
        finally:
            proxy.close()


def test_stream_does_not_retry_after_meaningful_output(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0
    partial = (
        b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
        + b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        + b'data: {"error":{"message":"temporarily overloaded","type":"upstream_error"}}\n\n'
    )

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            nonlocal attempts
            attempts += 1
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(partial)
            self.wfile.flush()

        def log_message(self, format: str, *args: object) -> None:
            pass

    monkeypatch.setattr("areal_rsi.modules.gateway.time.sleep", lambda _: None)
    with local_upstream(Handler) as upstream:
        proxy = GatewayProxy(upstream, "offline-fixture")
        try:
            request = urllib.request.Request(
                proxy.url,
                data=b'{"model":"gateway-fixture-model","messages":[],"stream":true}',
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                body = response.read()
            assert attempts == 1
            assert b'"content": "partial"' in body
            assert b'"type": "upstream_error"' in body
            assert b"data: [DONE]" not in body
            assert "stream_retries" not in proxy.requests[0]
        finally:
            proxy.close()


def test_ordinary_json_is_preserved_and_request_disables_storage() -> None:
    response_body = '{"choices":[],"reply":"你好","usage":{"total_tokens":5}}'.encode()
    original = {
        "model": "gateway-fixture-model",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 7,
        "thinking": True,
        "reasoning_effort": "high",
        "dsh_plugin_packages": ["fixture"],
        "dsh_session_log": "fixture",
    }
    expected = {key: original[key] for key in ("model", "messages", "max_tokens")}
    expected["store"] = False
    forwarded: list[object] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            forwarded.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    with local_upstream(Handler) as upstream:
        proxy = GatewayProxy(upstream, "offline-fixture")
        try:
            request = urllib.request.Request(
                proxy.url,
                data=json.dumps(original).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                assert int(response.headers["Content-Length"]) == len(response_body)
                assert response.read() == response_body
            assert forwarded == [expected]
            assert proxy.requests == [
                {
                    "request": expected,
                    "removed_fields": [
                        "thinking",
                        "reasoning_effort",
                        "dsh_plugin_packages",
                        "dsh_session_log",
                    ],
                    "content_type": "application/json",
                    "status": 200,
                    "response": response_body.decode(),
                }
            ]
        finally:
            proxy.close()


@pytest.mark.parametrize("api_key", ["", "test-only-sentinel"])
def test_authentication_stays_between_proxy_and_configured_service(api_key: str) -> None:
    received: list[str | None] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers["Content-Length"]))
            received.append(self.headers.get("Authorization"))
            body = b'{"choices": []}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    proxy = GatewayProxy(f"http://127.0.0.1:{server.server_port}/v1", api_key)
    try:
        request = urllib.request.Request(
            proxy.url + "/chat/completions",
            data=json.dumps({"model": "gateway-fixture-model", "messages": []}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            assert response.status == 200
        assert received == [f"Bearer {api_key}" if api_key else None]
        assert "test-only-sentinel" not in json.dumps(proxy.requests)
        assert "Authorization" not in json.dumps(proxy.requests)
        assert proxy.requests[0]["request"]["store"] is False
    finally:
        proxy.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
