import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from areal_rsi.modules.gateway import GatewayProxy, normalize_tool_deltas


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
            data=json.dumps({"model": "test-model", "messages": []}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            assert response.status == 200
        assert received == [f"Bearer {api_key}" if api_key else None]
        assert "test-only-sentinel" not in json.dumps(proxy.requests)
        assert "Authorization" not in json.dumps(proxy.requests)
    finally:
        proxy.close()
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
