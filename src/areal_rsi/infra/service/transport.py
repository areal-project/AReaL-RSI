"""Local Unix-socket RPC and background service process lifecycle."""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path
from typing import Any

from areal_rsi.api.control import ServiceRequest, decode_record, encode_record, public_tool_text
from areal_rsi.infra.config import data_root

WIRE_LIMIT = 4 * 1024 * 1024


def socket_path(root: Path) -> Path:
    # macOS limits Unix socket paths to 104 bytes; data directories may be longer.
    directory = Path(f"/tmp/areal-rsi-{os.getuid()}")
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink() or directory.stat().st_uid != os.getuid():
        raise ValueError("Unsafe RSI socket directory")
    directory.chmod(0o700)
    return directory / (hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:24] + ".sock")


async def serve(root: Path) -> None:
    from areal_rsi.infra.service.handler import SessionService

    root = data_root(root)
    directory = root / "service"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / "owner.lock").open("a+") as lease:
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        service = SessionService(root)
        connections: set[asyncio.Task[None]] = set()
        path = socket_path(root)

        async def connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                try:
                    raw = await asyncio.wait_for(reader.readline(), 30)
                    request = decode_record(ServiceRequest, json.loads(raw))
                    result = {"ok": True, "result": await service.handle(request)}
                except Exception as exc:
                    result = {"ok": False, "error": public_tool_text(str(exc))}
                writer.write(json.dumps(result, ensure_ascii=False).encode() + b"\n")
                await writer.drain()
            except (ConnectionError, BrokenPipeError):
                pass  # Accepted mutations continue without their caller.
            finally:
                writer.close()
                with contextlib.suppress(ConnectionError):
                    await writer.wait_closed()

        def accepted(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            task = asyncio.create_task(connection(reader, writer))
            connections.add(task)
            task.add_done_callback(connections.discard)

        try:
            service.recover()
            path.unlink(missing_ok=True)
            server = await asyncio.start_unix_server(accepted, path=path, limit=WIRE_LIMIT)
            path.chmod(0o600)
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, service.shutdown.set)
            async with server:
                await service.shutdown.wait()
        finally:
            for task in connections:
                task.cancel()
            await asyncio.gather(*connections, return_exceptions=True)
            await service.close()
            path.unlink(missing_ok=True)


async def call(
    root: Path, operation: str, arguments: dict[str, Any] | None = None, *, autostart: bool = True
) -> dict[str, Any]:
    """One local RPC; never retry a mutation after an ambiguous connection failure."""
    root = data_root(root)
    path = socket_path(root)
    try:
        reader, writer = await asyncio.open_unix_connection(path, limit=WIRE_LIMIT)
    except (FileNotFoundError, ConnectionRefusedError):
        if not autostart:
            raise ValueError("RSI service is not running") from None
        directory = root / "service"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (directory / "service.log").open("ab") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "areal_rsi", "service", "serve", "--data-root", str(root)],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
        for _ in range(100):
            await asyncio.sleep(0.1)
            process.poll()  # Reap a losing startup race without touching the winning daemon.
            try:
                reader, writer = await asyncio.open_unix_connection(path, limit=WIRE_LIMIT)
                break
            except (FileNotFoundError, ConnectionRefusedError):
                continue
        else:
            raise RuntimeError(f"Service failed to start; inspect {directory / 'service.log'}")
    try:
        writer.write(
            json.dumps(
                encode_record(
                    ServiceRequest(operation, arguments or {}, client_version=version("areal-rsi"))
                )
            ).encode()
            + b"\n"
        )
        await writer.drain()
        raw = await asyncio.wait_for(reader.readline(), 30)
        if not raw:
            raise RuntimeError("Service disconnected; query the same request_id before retrying")
        response = json.loads(raw)
        if not response["ok"]:
            raise ValueError(response["error"])
        return dict(response["result"])
    except TimeoutError:
        raise TimeoutError(
            "RSI service request timed out; work may still be running. "
            "Query the existing request_id before retrying a mutation."
        ) from None
    finally:
        writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()
