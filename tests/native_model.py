"""Offline Responses fixture. The real Codex binary still owns tools, turns and history."""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

Json = dict[str, Any]


class NativeModel:
    def __init__(self, respond: Callable[[Json], str | list[Json]]):
        self.respond = respond
        self.requests: list[Json] = []
        self.errors: list[str] = []
        self.catalog: Json = {
            "object": "list",
            "data": [
                {"id": "gpt-6-sol", "owned_by": "fixture"},
                {"id": "gpt-6-astra"},
                {"id": "gpt-6-luna"},
                {"id": "gpt-6-sol"},
            ],
        }
        self.catalog_status = 200
        self.catalog_requests: list[tuple[str, str | None]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                owner.catalog_requests.append((self.path, self.headers.get("Authorization")))
                body = json.dumps(owner.catalog).encode()
                self.send_response(owner.catalog_status if self.path == "/v1/models" else 404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                try:
                    value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    owner.requests.append(value)
                    reply = owner.respond(value)
                    response_id = "resp_" + uuid.uuid4().hex
                    items: list[Json]
                    if isinstance(reply, str):
                        items = [
                            {
                                "type": "message",
                                "id": "msg_" + uuid.uuid4().hex,
                                "role": "assistant",
                                "status": "completed",
                                "content": [
                                    {"type": "output_text", "text": reply, "annotations": []}
                                ],
                            }
                        ]
                    else:
                        items = reply
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.send_event(
                        "response.created",
                        {
                            "response": {
                                "id": response_id,
                                "status": "in_progress",
                                "output": [],
                            }
                        },
                    )
                    for index, item in enumerate(items):
                        self.send_event(
                            "response.output_item.added",
                            {
                                "output_index": index,
                                "item": item,
                            },
                        )
                        if item["type"] == "message":
                            self.send_event(
                                "response.output_text.delta",
                                {
                                    "item_id": item["id"],
                                    "output_index": index,
                                    "content_index": 0,
                                    "delta": item["content"][0]["text"],
                                },
                            )
                        self.send_event(
                            "response.output_item.done",
                            {
                                "output_index": index,
                                "item": item,
                            },
                        )
                    self.send_event(
                        "response.completed",
                        {
                            "response": {
                                "id": response_id,
                                "status": "completed",
                                "output": items,
                                "usage": {
                                    "input_tokens": 20,
                                    "output_tokens": 10,
                                    "total_tokens": 30,
                                },
                            }
                        },
                    )
                    self.wfile.flush()
                    self.close_connection = True
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception as exc:
                    owner.errors.append(repr(exc))
                    self.send_error(500)

            def send_event(self, kind: str, fields: Json) -> None:
                value = {"type": kind, **fields}
                self.wfile.write((f"event: {kind}\ndata: " + json.dumps(value) + "\n\n").encode())

            def log_message(self, format: str, *args: Any) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def function(name: str, arguments: Json) -> list[Json]:
    return [
        {
            "type": "function_call",
            "id": "fc_" + uuid.uuid4().hex,
            "call_id": "call_" + uuid.uuid4().hex,
            "name": name,
            "arguments": json.dumps(arguments),
        }
    ]
