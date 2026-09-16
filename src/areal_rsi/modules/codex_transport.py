"""Async app-server transport and an accounted, credential-isolating model gateway.

Use the Python SDK's pinned native runtime and the documented JSON-RPC protocol.
The SDK's synchronous server-request callback blocks its reader; our dispatcher
keeps reading while a domain tool runs, so steering and interruption cannot deadlock.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import signal
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

import httpx
from codex_cli_bin import bundled_codex_path  # type: ignore[import-untyped]

from areal_rsi.base.storage import new_id, write_json
from areal_rsi.modules.agent_runtime import AgentRuntimeError, EventSink, RuntimeConfig

Json = dict[str, Any]
ServerHandler = Callable[[str, Json], Awaitable[Json]]


class AppServer:
    def __init__(self, directory: Path, environment: dict[str, str], handler: ServerHandler):
        self.directory, self.environment, self.handler = directory, environment, handler
        self.events: asyncio.Queue[Json | Exception] = asyncio.Queue()
        self.pending: dict[str, asyncio.Future[Json]] = {}
        self.handlers: set[asyncio.Task[None]] = set()
        self.process: asyncio.subprocess.Process | None = None
        self.reader: asyncio.Task[None] | None = None
        self.stderr: Any = None
        self.lock = asyncio.Lock()

    async def start(self) -> None:
        self.stderr = (self.directory / "app-server.stderr.log").open("ab")
        self.process = await asyncio.create_subprocess_exec(
            str(bundled_codex_path()),
            "app-server",
            "--listen",
            "stdio://",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=self.stderr,
            env=self.environment,
            cwd=self.directory,
            start_new_session=True,
            limit=32 * 1024 * 1024,
        )
        self.reader = asyncio.create_task(self._read())
        await self.request(
            "initialize",
            {
                "clientInfo": {"name": "areal_rsi", "title": "AReaL RSI", "version": "0.1.0"},
                "capabilities": {"experimentalApi": True},
            },
        )
        await self._write({"method": "initialized"})

    async def _write(self, packet: Json) -> None:
        if self.process is None or self.process.stdin is None:
            raise AgentRuntimeError("Codex app-server is closed")
        async with self.lock:
            self.process.stdin.write((json.dumps(packet) + "\n").encode())
            await self.process.stdin.drain()

    async def request(self, method: str, params: Json) -> Json:
        key = new_id("rpc")
        future = asyncio.get_running_loop().create_future()
        self.pending[key] = future
        try:
            await self._write({"id": key, "method": method, "params": params})
            return await asyncio.wait_for(future, timeout=60)
        finally:
            self.pending.pop(key, None)

    async def _handle(self, packet: Json) -> None:
        try:
            result = await self.handler(packet["method"], packet.get("params", {}))
            await self._write({"id": packet["id"], "result": result})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            with suppress(Exception):
                await self._write(
                    {
                        "id": packet["id"],
                        "error": {
                            "code": -32603,
                            "message": str(exc),
                        },
                    }
                )

    async def _read(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            while line := await self.process.stdout.readline():
                packet = json.loads(line)
                if "method" in packet and "id" in packet:
                    task = asyncio.create_task(self._handle(packet))
                    self.handlers.add(task)
                    task.add_done_callback(self.handlers.discard)
                elif "method" in packet:
                    self.events.put_nowait(packet)
                elif (future := self.pending.get(str(packet.get("id")))) and not future.done():
                    if "error" in packet:
                        future.set_exception(
                            AgentRuntimeError(
                                "Codex RPC: " + json.dumps(packet["error"], ensure_ascii=False)
                            )
                        )
                    else:
                        future.set_result(packet.get("result", {}))
            raise AgentRuntimeError("Codex app-server disconnected; resume this conversation")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(exc)
            self.events.put_nowait(exc)

    async def close(self) -> None:
        for task in tuple(self.handlers):
            task.cancel()
        await asyncio.gather(*self.handlers, return_exceptions=True)
        process, self.process = self.process, None
        if process is not None:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
        if self.reader is not None:
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
        if self.stderr is not None:
            self.stderr.close()


class CodexGateway:
    """Account each real request before forwarding; never replay model requests here."""

    def __init__(
        self,
        config: RuntimeConfig,
        directory: Path,
        emit: EventSink,
        reserve: Callable[[str], None],
        *,
        allow_web_access: bool = True,
    ):
        self.config, self.directory, self.emit, self.reserve = config, directory, emit, reserve
        self.allow_web_access = allow_web_access
        self.token = secrets.token_urlsafe(32)
        self.active = True
        self.error: Exception | None = None
        self.server: asyncio.Server | None = None
        self.clients: set[asyncio.Task[Any]] = set()
        self.http = httpx.AsyncClient(timeout=config.timeout)
        self.url = ""

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0, limit=65536)
        port = self.server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}/v1"

    async def close(self) -> None:
        self.active = False
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        for task in tuple(self.clients):
            task.cancel()
        await asyncio.gather(*self.clients, return_exceptions=True)
        await self.http.aclose()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        self.clients.add(task)
        headers_sent = False
        identifier: str | None = None
        error_directory = self.directory / "responses"
        try:
            head = (await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 30)).decode()
            first, *lines = head.split("\r\n")
            headers = {
                k.lower(): v.strip()
                for line in lines
                if ":" in line
                for k, v in [line.split(":", 1)]
            }
            if headers.get("authorization") != "Bearer " + self.token:
                raise ValueError("Invalid gateway credential")
            request = first.split()[:2]
            search = request == ["POST", "/v1/alpha/search"]
            if request != ["POST", "/v1/responses"] and not search:
                raise ValueError("Only Responses and native web-search requests are supported")
            length = int(headers.get("content-length", "0"))
            if not 0 < length <= 64 * 1024 * 1024:
                raise ValueError("Invalid Responses request size")
            if headers.get("content-encoding", "identity") != "identity":
                raise ValueError("Compressed model requests must be disabled")
            payload = json.loads(await reader.readexactly(length))
            if not self.active:
                raise ValueError("Agent execution is paused or stopped")
            if search:
                if not self.allow_web_access:
                    raise ValueError("Web access is disabled for this research")
                identifier = new_id("codex_search")
                web = self.directory / "web"
                error_directory = web / "responses"
                write_json(web / "requests" / f"{identifier}.json", payload)
                await self.emit({"type": "web_search_request", "request_id": identifier})
                # Keep Codex's search protocol intact. This is a hosted tool
                # request, not another model inference or a custom search engine.
                response = await self.http.post(
                    self.config.endpoint.removesuffix("/responses") + "/alpha/search",
                    json=payload,
                    headers={"Authorization": "Bearer " + self.config.api_key},
                )
                error_directory.mkdir(parents=True, exist_ok=True)
                (error_directory / f"{identifier}.body").write_bytes(response.content)
                record = {
                    "request_id": identifier,
                    "status_code": response.status_code,
                    "content_type": response.headers.get("content-type", "application/json"),
                }
                write_json(error_directory / f"{identifier}.json", record)
                await self.emit({"type": "web_search_response", **record})
                writer.write(
                    (
                        f"HTTP/1.1 {response.status_code} Response\r\n"
                        f"Content-Type: {record['content_type']}\r\n"
                        f"Content-Length: {len(response.content)}\r\nConnection: close\r\n\r\n"
                    ).encode()
                    + response.content
                )
                headers_sent = True
                await writer.drain()
                return
            identifier = new_id("codex_request")
            # Honor the same explicit output reservation as the standard backend.
            payload["max_output_tokens"] = self.config.max_output_tokens
            write_json(self.directory / "requests" / f"{identifier}.json", payload)
            self.reserve(identifier)
            try:
                await self.emit(
                    {
                        "type": "model_request",
                        "request_id": identifier,
                        "kind": "codex",
                        "backend": "codex",
                    }
                )
                await self.emit({"type": "model_attempt", "request_id": identifier, "attempt": 1})
            except Exception as exc:
                self.error = exc
                raise
            async with self.http.stream(
                "POST",
                self.config.endpoint,
                json=payload,
                headers={
                    "Authorization": "Bearer " + self.config.api_key,
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                },
            ) as response:
                content_type = response.headers.get("content-type", "application/json")
                writer.write(
                    (
                        f"HTTP/1.1 {response.status_code} Response\r\n"
                        f"Content-Type: {content_type}\r\n"
                        "Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
                    ).encode()
                )
                headers_sent = True
                pending = bytearray()
                with (self.directory / "responses" / f"{identifier}.sse").open("ab") as archive:
                    async for chunk in response.aiter_bytes():
                        archive.write(chunk)
                        archive.flush()
                        pending.extend(chunk)
                        while b"\n" in pending:
                            line, _, pending = pending.partition(b"\n")
                            if line.startswith(b"data:"):
                                with suppress(ValueError):
                                    event = json.loads(line[5:])
                                    terminal = event.get("response", {})
                                    if event.get("type") in {
                                        "response.completed",
                                        "response.incomplete",
                                        "response.failed",
                                    }:
                                        write_json(
                                            self.directory / "responses" / f"{identifier}.json",
                                            terminal,
                                        )
                                        if terminal.get("usage"):
                                            await self.emit(
                                                {
                                                    "type": "response_usage",
                                                    "request_id": identifier,
                                                    "usage": terminal["usage"],
                                                }
                                            )
                        writer.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                        await writer.drain()
                writer.write(b"0\r\n\r\n")
                await writer.drain()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message = str(exc).replace(self.config.api_key, "[redacted]") or type(exc).__name__
            transient = isinstance(exc, httpx.TransportError)
            if transient:
                # HTTPX connection/read errors may have an empty message. Keep
                # their type and retryability instead of presenting a fatal
                # invalid request to Codex. This also covers broken SSE streams
                # after response headers have already reached the client.
                self.error = AgentRuntimeError(
                    f"Codex upstream transport error ({type(exc).__name__}): {message}"
                )
                if identifier:
                    write_json(
                        error_directory / f"{identifier}.error.json",
                        {"error_type": type(exc).__name__, "message": message, "retryable": True},
                    )
            if not headers_sent:
                body = json.dumps(
                    {
                        "error": {
                            "type": "transport_error" if transient else "invalid_request_error",
                            "message": message,
                        }
                    }
                ).encode()
                writer.write(
                    (
                        b"HTTP/1.1 502 Bad Gateway\r\n"
                        if transient
                        else b"HTTP/1.1 400 Bad Request\r\n"
                    )
                    + b"Content-Type: application/json\r\n"
                    + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                    + body
                )
                with suppress(Exception):
                    await writer.drain()
        finally:
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()
            self.clients.discard(task)
