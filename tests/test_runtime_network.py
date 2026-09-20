"""The native HTTPS transport must not expose local run services."""

import asyncio
import socket
from typing import Any

import pytest

from areal_rsi.infra.runtime.runtime_network import HttpsTunnel


def test_https_tunnel_rejects_private_destinations() -> None:
    async def scenario() -> None:
        tunnel = HttpsTunnel()
        port = await tunnel.start()
        try:
            for target in ("127.0.0.1:443", "localhost:443", "[::1]:443", "example.com:8198"):
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.write(f"CONNECT {target} HTTP/1.1\r\n\r\n".encode())
                await writer.drain()
                assert (await reader.read()).startswith(b"HTTP/1.1 403")
                writer.close()
                await writer.wait_closed()
        finally:
            await tunnel.close()

    asyncio.run(scenario())


def test_https_tunnel_pins_public_address_and_preserves_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            writer.write(await reader.readexactly(4))
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(echo, "127.0.0.1", 0)
        origin = server.sockets[0].getsockname()[1]
        connect = asyncio.open_connection

        async def resolve(*args: Any, **kwargs: Any) -> list[Any]:
            assert args == ("example.com", 443)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

        async def pinned(host: str, port: int) -> Any:
            assert (host, port) == ("93.184.216.34", 443)
            return await connect("127.0.0.1", origin)

        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
        monkeypatch.setattr(asyncio, "open_connection", pinned)
        tunnel = HttpsTunnel()
        port = await tunnel.start()
        try:
            reader, writer = await connect("127.0.0.1", port)
            writer.write(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
            await writer.drain()
            assert b" 200 " in await reader.readuntil(b"\r\n\r\n")
            writer.write(b"\x00\xff\x80\x01")
            await writer.drain()
            assert await reader.readexactly(4) == b"\x00\xff\x80\x01"
            writer.close()
            await writer.wait_closed()
        finally:
            await tunnel.close()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())
