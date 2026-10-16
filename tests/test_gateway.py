import json
import threading
import urllib.request
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from areal_rsi.modules.gateway import ChatStreamBatcher, GatewayProxy, normalize_tool_deltas


def test_chat_batching_preserves_tool_arguments_text_usage_and_boundaries() -> None:
    batcher = ChatStreamBatcher(max_chars=64, interval=3600)
    events: list[dict[str, Any]] = [{"choices": [{"index": 0, "delta": {"role": "assistant"}}]}]
    for call_index in range(2):
        events.append(
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": call_index,
                                    "id": f"call_{call_index}",
                                    "function": {"name": "tool", "arguments": ""},
                                }
                            ]
                        },
                    }
                ]
            }
        )
        for character in '{"text":"你好\\n"}':
            events.append(
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": call_index,
                                        "id": None,
                                        "function": {"name": None, "arguments": character},
                                    }
                                ]
                            },
                        }
                    ]
                }
            )
    for character in "final response":
        events.append({"choices": [{"index": 0, "delta": {"content": character}}]})
    events.extend(
        [
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"completion_tokens": 42}},
        ]
    )
    output = []
    for event in events:
        output.extend(batcher.push(("data: " + json.dumps(event) + "\n\n").encode()))
    output.extend(batcher.push(b"data: [DONE]\n\n"))
    output.extend(batcher.flush())
    chunks = [json.loads(x.decode().strip()[6:]) for x in output[:-1]]
    assert len(chunks) < 12
    arguments = ["", ""]
    text = ""
    for chunk in chunks:
        for choice in chunk["choices"]:
            delta = choice["delta"]
            text += delta.get("content", "")
            for call in delta.get("tool_calls", []):
                arguments[call["index"]] += call["function"]["arguments"]
    assert arguments == ['{"text":"你好\\n"}'] * 2
    assert text == "final response"
    assert chunks[-2:] == events[-2:]
    assert output[-1].strip() == b"data: [DONE]"


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


def test_confirmed_model_cannot_be_changed_through_proxy() -> None:
    import urllib.error

    # A request for another model must fail locally, before any upstream connection.
    proxy = GatewayProxy("http://127.0.0.1:1/v1", "private-key", model="fixed-model")
    try:
        request = urllib.request.Request(
            proxy.url + "/v1/chat/completions",
            data=json.dumps({"model": "another-model", "messages": []}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=3)
        assert error.value.code == 403
        assert not proxy.requests
    finally:
        proxy.close()
