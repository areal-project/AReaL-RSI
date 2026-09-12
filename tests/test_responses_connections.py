"""Exercise connection reuse and concurrent cancellation, without external APIs."""

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx

from areal_rsi.modules.responses import (
    HttpResponsesTransport,
    ResponsesConfig,
    ResponsesRuntime,
    ToolCall,
    ToolResult,
)


def response(output: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": "response_test",
        "status": "completed",
        "output": output,
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    }


FINAL = response(
    [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "完成"}]}]
)


def test_one_connection_serves_retry_and_subsequent_tool_turns(tmp_path: Path) -> None:
    async def scenario() -> None:
        connections = requests = effects = 0
        closed = asyncio.Event()

        async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            nonlocal connections, requests
            connections += 1
            try:
                while await reader.readline():
                    headers: dict[str, str] = {}
                    while (line := await reader.readline()) not in {b"\r\n", b""}:
                        key, header_value = line.decode().split(":", 1)
                        headers[key.lower()] = header_value.strip()
                    await reader.readexactly(int(headers["content-length"]))
                    requests += 1
                    if requests == 1:
                        status, value = "503 Service Unavailable", {"error": "temporary"}
                    elif requests == 2:
                        status, value = (
                            "200 OK",
                            response(
                                [
                                    {
                                        "type": "function_call",
                                        "name": "inspect",
                                        "call_id": "inspect_once",
                                        "arguments": "{}",
                                    }
                                ]
                            ),
                        )
                    else:
                        status, value = "200 OK", FINAL
                    body = json.dumps(value).encode()
                    writer.write(
                        f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\n"
                        f"Content-Length: {len(body)}\r\n\r\n".encode()
                        + body
                    )
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                closed.set()

        async def handle(_: ToolCall) -> ToolResult:
            nonlocal effects
            effects += 1
            return ToolResult({"checked": True})

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        async with server:
            port = server.sockets[0].getsockname()[1]
            runtime = ResponsesRuntime(
                ResponsesConfig(
                    base_url=f"http://127.0.0.1:{port}",
                    api_key="local-test",
                    retry_initial_seconds=0.001,
                    retry_max_seconds=0.001,
                )
            )
            result = await runtime.turn(
                tmp_path / "session",
                system_prompt="测试",
                user_message="检查",
                message_id="one",
                tools=[
                    {
                        "type": "function",
                        "name": "inspect",
                        "description": "检查",
                        "parameters": {"type": "object", "properties": {}},
                    }
                ],
                handler=handle,
            )
            await asyncio.wait_for(closed.wait(), 2)
        assert result.text == "完成"
        assert (connections, requests, effects) == (1, 3, 1)

    asyncio.run(scenario())


def test_cancelling_one_actor_keeps_other_actor_connection_alive(tmp_path: Path) -> None:
    async def scenario() -> None:
        class Stream(httpx.AsyncByteStream):
            def __init__(self) -> None:
                self.started = asyncio.Event()
                self.release = asyncio.Event()
                self.closed = False

            async def __aiter__(self) -> AsyncIterator[bytes]:
                self.started.set()
                await self.release.wait()
                yield (
                    "data: "
                    + json.dumps({"type": "response.completed", "response": FINAL})
                    + "\n\n"
                ).encode()

            async def aclose(self) -> None:
                self.closed = True

        class Transport(httpx.AsyncBaseTransport):
            def __init__(self) -> None:
                self.streams = [Stream(), Stream()]
                self.calls = 0
                self.closed = 0

            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                stream = self.streams[self.calls]
                self.calls += 1
                return httpx.Response(
                    200, headers={"content-type": "text/event-stream"}, stream=stream
                )

            async def aclose(self) -> None:
                self.closed += 1

        transport = Transport()
        runtime = ResponsesRuntime(
            ResponsesConfig(base_url="https://example.test", api_key="test"),
            HttpResponsesTransport(transport),
        )

        async def handle(_: ToolCall) -> ToolResult:
            raise AssertionError("No tools expected")

        async def turn(name: str) -> str:
            result = await runtime.turn(
                tmp_path / name,
                system_prompt="测试",
                user_message=name,
                message_id=name,
                tools=[],
                handler=handle,
            )
            return result.text

        first = asyncio.create_task(turn("first"))
        await asyncio.wait_for(transport.streams[0].started.wait(), 2)
        second = asyncio.create_task(turn("second"))
        await asyncio.wait_for(transport.streams[1].started.wait(), 2)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        assert transport.streams[0].closed
        assert transport.closed == 0
        assert not second.done()
        transport.streams[1].release.set()
        assert await asyncio.wait_for(second, 2) == "完成"
        assert transport.streams[1].closed
        assert transport.closed == 1

    asyncio.run(scenario())
