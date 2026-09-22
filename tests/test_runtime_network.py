"""The native HTTPS transport must not expose local run services."""

import asyncio
import socket
import threading
from typing import Any

import pytest

from areal_rsi.modules.runtime_network import HttpsTunnel, _TunnelServer


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
        tunnel = _TunnelServer()
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


def test_https_relay_keeps_flowing_when_owner_loop_is_blocked() -> None:
    with socket.create_server(("127.0.0.1", 0)) as origin:
        origin.settimeout(5)
        target = f"127.0.0.1:{origin.getsockname()[1]}"
        done = threading.Event()
        received = []

        def exchange(port: int) -> None:
            with socket.create_connection(("127.0.0.1", port), timeout=3) as client:
                client.sendall(f"CONNECT {target} HTTP/1.1\r\n\r\n".encode())
                upstream, _ = origin.accept()
                with upstream:
                    assert b"200" in client.recv(1024)
                    client.sendall(b"request")
                    assert upstream.recv(7) == b"request"
                    upstream.sendall(b"response")
                    received.append(client.recv(8))
            done.set()

        async def scenario() -> None:
            tunnel = HttpsTunnel("https://" + target)
            port = await tunnel.start()
            process = tunnel.process
            thread = threading.Thread(target=exchange, args=(port,), daemon=True)
            thread.start()
            try:
                # Deliberately block the owning event loop, as file/UI work did.
                assert done.wait(4), "Relay stalled with its owner's event loop"
                assert received == [b"response"]
            finally:
                await tunnel.close()
                thread.join(1)
            assert process is not None and process.returncode == 0

        asyncio.run(scenario())


@pytest.mark.parametrize("upstream_closes", [False, True])
def test_https_tunnel_releases_client_on_silence_or_upstream_eof(
    upstream_closes: bool,
) -> None:
    async def scenario() -> None:
        disconnected = asyncio.Event()

        async def origin(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            assert await reader.readexactly(4) == b"ping"
            writer.write(b"pong")
            await writer.drain()
            if not upstream_closes:
                assert await reader.read() == b""
            writer.close()
            await writer.wait_closed()
            disconnected.set()

        server = await asyncio.start_server(origin, "127.0.0.1", 0)
        target = f"127.0.0.1:{server.sockets[0].getsockname()[1]}"
        tunnel = _TunnelServer("https://" + target, idle_timeout=0.1)
        port = await tunnel.start()
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(f"CONNECT {target} HTTP/1.1\r\n\r\n".encode())
            await writer.drain()
            assert b" 200 " in await reader.readuntil(b"\r\n\r\n")
            writer.write(b"ping")
            await writer.drain()
            assert await reader.readexactly(4) == b"pong"
            # No client EOF: the relay must terminate the dead upstream itself.
            assert await asyncio.wait_for(reader.read(), 2) == b""
            await asyncio.wait_for(disconnected.wait(), 2)
            writer.close()
            await writer.wait_closed()
        finally:
            await tunnel.close()
            server.close()
            await server.wait_closed()
        assert not tunnel.tasks

    asyncio.run(scenario())


def test_https_tunnel_idle_timeout_resets_on_traffic() -> None:
    async def scenario() -> None:
        async def origin(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            while data := await reader.read(1024):
                writer.write(data)
                await writer.drain()
            writer.close()

        server = await asyncio.start_server(origin, "127.0.0.1", 0)
        target = f"127.0.0.1:{server.sockets[0].getsockname()[1]}"
        tunnel = _TunnelServer("https://" + target, idle_timeout=0.3)
        port = await tunnel.start()
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(f"CONNECT {target} HTTP/1.1\r\n\r\n".encode())
            await writer.drain()
            assert b" 200 " in await reader.readuntil(b"\r\n\r\n")
            for _ in range(6):
                await asyncio.sleep(0.1)
                writer.write(b"ping")
                await writer.drain()
                assert await asyncio.wait_for(reader.readexactly(4), 1) == b"ping"
            writer.close()
            await writer.wait_closed()
        finally:
            await tunnel.close()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())
