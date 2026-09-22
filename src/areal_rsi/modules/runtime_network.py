"""Public HTTPS tunnelling for native clients inside the macOS file sandbox.

TLS remains end-to-end. Resolve and pin public destination addresses in the
relay so this listener cannot become a route into another run's local services.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import sys
import time
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlsplit


class _TunnelServer:
    def __init__(self, base_url: str = "", *, idle_timeout: float = 300) -> None:
        provider = urlsplit(base_url)
        self.provider = (provider.hostname, provider.port or 443)
        self.server: asyncio.Server | None = None
        self.tasks: set[asyncio.Task[None]] = set()
        self.idle_timeout = idle_timeout

    async def start(self) -> int:
        if self.server is None:
            self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0, limit=16384)
        return int(self.server.sockets[0].getsockname()[1])

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        self.tasks.add(task)
        remote: asyncio.StreamWriter | None = None
        pumps: list[asyncio.Task[None]] = []
        started = time.time()
        transferred = [0, 0]
        target = ""
        outcome = "closed"
        try:
            async with asyncio.timeout(10):
                header = await reader.readuntil(b"\r\n\r\n")
                method, target, _ = header.split(b"\r\n", 1)[0].decode("ascii").split()
                endpoint = urlsplit("//" + target)
                trusted = (endpoint.hostname, endpoint.port) == self.provider
                if (
                    method != "CONNECT"
                    or not endpoint.hostname
                    or (endpoint.port != 443 and not trusted)
                ):
                    raise ValueError("Only public HTTPS is supported")
                addresses = await asyncio.get_running_loop().getaddrinfo(
                    endpoint.hostname, endpoint.port, type=socket.SOCK_STREAM
                )
                if not trusted and not all(
                    ipaddress.ip_address(row[4][0]).is_global for row in addresses
                ):
                    raise ValueError("Private destinations are not allowed")
                upstream, remote = await asyncio.open_connection(
                    str(addresses[0][4][0]), endpoint.port
                )
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()

            # Bound silence across both directions, including TLS/HTTP setup before
            # Codex's SSE timeout starts. Active long-running streams stay open.
            loop = asyncio.get_running_loop()
            async with asyncio.timeout(self.idle_timeout) as deadline:

                async def pipe(
                    source: asyncio.StreamReader,
                    destination: asyncio.StreamWriter,
                    direction: int,
                ) -> None:
                    while data := await source.read(65536):
                        destination.write(data)
                        await destination.drain()
                        transferred[direction] += len(data)
                        deadline.reschedule(loop.time() + self.idle_timeout)

                pumps = [
                    asyncio.create_task(pipe(reader, remote, 0)),
                    asyncio.create_task(pipe(upstream, writer, 1)),
                ]
                done, _ = await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
                for completed in done:
                    completed.result()
        except (
            OSError,
            ValueError,
            TimeoutError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ) as exc:
            outcome = type(exc).__name__
            if remote is None:
                writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
                with suppress(OSError):
                    await writer.drain()
        finally:
            # Finish ordinary EOFs without losing buffered response bytes. Bound
            # the flush as well: a dead peer must not retain a half-open tunnel.
            writer.close()
            if remote is not None:
                remote.close()
            for pump in pumps:
                pump.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            try:
                with suppress(OSError, TimeoutError):
                    async with asyncio.timeout(2):
                        await writer.wait_closed()
                        if remote is not None:
                            await remote.wait_closed()
            finally:
                writer.transport.abort()
                if remote is not None:
                    remote.transport.abort()
            self.tasks.discard(task)
            print(
                json.dumps(
                    {
                        "at": time.time(),
                        "started_at": started,
                        "target": target,
                        "outcome": outcome,
                        "bytes_up": transferred[0],
                        "bytes_down": transferred[1],
                    }
                ),
                file=sys.stderr,
                flush=True,
            )

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class HttpsTunnel:
    """Own a relay process so synchronous research/UI work cannot stall TLS."""

    def __init__(self, base_url: str = "", *, event_log: Path | None = None) -> None:
        self.base_url = base_url
        self.event_log = event_log
        self.process: asyncio.subprocess.Process | None = None
        self.port: int | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> int:
        async with self._lock:
            if self.process is None:
                with (
                    self.event_log.open("ab") if self.event_log else open("/dev/null", "ab") as log
                ):
                    self.process = await asyncio.create_subprocess_exec(
                        sys.executable,
                        "-I",
                        str(Path(__file__).resolve()),
                        self.base_url,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=log,
                    )
                assert self.process.stdout is not None
                try:
                    self.port = int(await asyncio.wait_for(self.process.stdout.readline(), 10))
                except BaseException:
                    await self.close()
                    raise
            assert self.port is not None
            return self.port

    async def close(self) -> None:
        process, self.process = self.process, None
        self.port = None
        if process is None:
            return
        assert process.stdin is not None
        process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), 5)
        except TimeoutError:
            process.kill()
            await process.wait()


async def _main() -> None:
    server = _TunnelServer(sys.argv[1])
    try:
        print(await server.start(), flush=True)
        # EOF also closes the relay if its owner exits unexpectedly.
        reader = asyncio.StreamReader()
        await asyncio.get_running_loop().connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), sys.stdin
        )
        await reader.read()
    finally:
        await server.close()


if __name__ == "__main__":
    asyncio.run(_main())
