"""A direct app-server JSON-RPC connection. Agent execution stays inside Codex."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from areal_rsi.api.control import public_tool_text
from areal_rsi.api.research import SessionConfig
from areal_rsi.infra.config import executable, runtime_environment

Json = dict[str, Any]


class CodexError(RuntimeError):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


@dataclass(slots=True)
class NativeEvent:
    method: str
    params: Json
    request_id: int | str | None = None


class CodexClient:
    def __init__(self, home: Path, log: Path, config: SessionConfig):
        self.home, self.log, self.config = home, log, config
        self.events: asyncio.Queue[NativeEvent] = asyncio.Queue()
        self.process: asyncio.subprocess.Process | None = None
        self.reader: asyncio.Task[None] | None = None
        self.pending: dict[int, asyncio.Future[Json]] = {}
        self.sequence = 0
        self.write_lock = asyncio.Lock()
        self.stderr: BinaryIO | None = None
        self.closing = False
        self.inhibitor: subprocess.Popen[bytes] | None = None
        self.inhibit_unavailable = False

    def keep_awake(self, active: bool) -> None:
        """Hold an idle-sleep assertion only while work is active, as Codex's TUI does."""
        if not active or not self.config.keep_awake:
            if self.inhibitor:
                child = self.inhibitor
                self.inhibitor = None
                with contextlib.suppress(ProcessLookupError):
                    child.terminate()
                try:
                    child.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
                if child.stdin:
                    child.stdin.close()
            return
        if self.inhibitor is not None and self.inhibitor.poll() is not None:
            if self.inhibitor.stdin:
                self.inhibitor.stdin.close()
            self.inhibitor = None
            self.inhibit_unavailable = True
        if self.inhibitor is not None or self.inhibit_unavailable:
            return
        if sys.platform == "darwin":
            command = ["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())]
        elif sys.platform == "linux" and shutil.which("systemd-inhibit"):
            command = [
                "systemd-inhibit",
                "--what=idle:sleep",
                "--who=AReaL RSI",
                "--why=Active agent task",
                "--mode=block",
                "cat",
            ]
        else:
            self.inhibit_unavailable = True
            return
        try:
            self.inhibitor = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=self.stderr,
            )
        except OSError:
            self.inhibit_unavailable = True

    async def start(self) -> None:
        binary = executable(self.config)
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        environment = {**runtime_environment(self.config), "CODEX_HOME": str(self.home)}
        probe = await asyncio.create_subprocess_exec(
            binary,
            "--version",
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(probe.communicate(), 10)
        except TimeoutError:
            probe.kill()
            await probe.wait()
            raise CodexError("Codex version check timed out") from None
        version = re.search(rb"(\d+)\.(\d+)\.(\d+)", stdout)
        if probe.returncode or not version or tuple(map(int, version.groups())) < (0, 162, 1):
            raise CodexError("Codex >= 0.162.1 is required for the context-preserving protocol")
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self.stderr = self.log.open("ab")
        self.log.chmod(0o600)
        overrides: Json = {
            "model_provider": "rsi",
            "model_providers.rsi.name": "RSI API",
            "model_providers.rsi.base_url": self.config.base_url,
            "model_providers.rsi.env_key": self.config.api_key_env,
            "model_providers.rsi.wire_api": "responses",
            "model_providers.rsi.requires_openai_auth": False,
            "model_providers.rsi.supports_websockets": False,
            "features.goals": False,
            "features.memories": False,
            "features.multi_agent": False,
            "agents.enabled": False,
            "sandbox_workspace_write.network_access": True,
        }
        arguments = [binary, "app-server", "--listen", "stdio://"]
        for name, value in overrides.items():
            arguments.extend(["-c", f"{name}={json.dumps(value, ensure_ascii=False)}"])
        try:
            self.process = await asyncio.create_subprocess_exec(
                *arguments,
                cwd=self.config.workspace,
                env=environment,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=self.stderr,
                start_new_session=True,
                limit=16 * 1024 * 1024,
            )
            self.reader = asyncio.create_task(self._read())
            await self.request(
                "initialize",
                {
                    "clientInfo": {"name": "areal-rsi", "version": "0.1.0"},
                    "capabilities": {"experimentalApi": True},
                },
            )
            await self._send({"method": "initialized"})
        except BaseException:
            await self.close()
            raise

    async def _send(self, value: Json) -> None:
        async with self.write_lock:
            if not self.process or self.process.returncode is not None or not self.process.stdin:
                raise CodexError("Codex process is not connected")
            self.process.stdin.write((json.dumps(value, ensure_ascii=False) + "\n").encode())
            await self.process.stdin.drain()

    async def request(self, method: str, params: Json, *, timeout: float = 60) -> Json:
        self.sequence += 1
        request_id = self.sequence
        future: asyncio.Future[Json] = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self._send({"id": request_id, "method": method, "params": params})
            return await asyncio.wait_for(future, timeout)
        except TimeoutError:
            raise CodexError(
                f"{method} timed out; its outcome must be checked before retry"
            ) from None
        finally:
            self.pending.pop(request_id, None)

    async def reply(self, request_id: int | str, result: Json) -> None:
        await self._send({"id": request_id, "result": result})

    async def reject(self, request_id: int | str, message: str) -> None:
        await self._send({"id": request_id, "error": {"code": -32601, "message": message}})

    async def _read(self) -> None:
        assert self.process and self.process.stdout
        error = "Codex disconnected; resume checks the saved turn before continuing"
        try:
            while line := await self.process.stdout.readline():
                value = json.loads(line)
                if "method" in value:
                    await self.events.put(
                        NativeEvent(value["method"], value.get("params") or {}, value.get("id"))
                    )
                    continue
                future = self.pending.get(value.get("id"))
                if future is None or future.done():
                    continue
                if "error" in value:
                    failure = value["error"]
                    future.set_exception(
                        CodexError(
                            public_tool_text(failure.get("message", "Codex RPC error")),
                            failure.get("code"),
                        )
                    )
                else:
                    future.set_result(value.get("result") or {})
        except (OSError, ValueError) as exc:
            error = public_tool_text(str(exc))
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(CodexError(error))
            if not self.closing:
                await self.events.put(NativeEvent("transport/closed", {"error": error}))

    async def close(self) -> None:
        self.closing = True
        self.keep_awake(False)
        if self.process and self.process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(self.process.pid, signal.SIGKILL)
                await self.process.wait()
        if self.reader:
            self.reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.reader
        if self.stderr:
            self.stderr.close()
