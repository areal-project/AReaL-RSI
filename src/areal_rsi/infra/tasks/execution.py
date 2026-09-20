"""DSH task execution with explicit file exports and a bounded target gateway."""

from __future__ import annotations

import asyncio
import json
import secrets
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from areal_rsi.api.records import ContentRef, SolutionVersion, Task, TaskResult
from areal_rsi.infra.runtime.dsh_runtime import DshAgentRuntime
from areal_rsi.infra.runtime.gateway import GatewayProxy
from areal_rsi.infra.storage.files import FileContentStore, new_id, safe_relative
from areal_rsi.infra.tasks.harness import LocalHarnessRuntime


@dataclass(frozen=True)
class ExportedTask:
    result: TaskResult
    files: dict[str, ContentRef]
    missing: tuple[str, ...]


class TaskExportError(ValueError):
    """The target produced an inadmissible output, not an infrastructure failure."""


class FileTaskExecutor:
    """A private workspace per call; export files only after the DSH session closes.

    This composes the existing runtime and preserves its sandbox. No candidate
    file is imported or executed while harvesting outputs.
    """

    def __init__(self, runtime: DshAgentRuntime, content: FileContentStore, root: Path):
        self.runtime, self.content, self.root = runtime, content, root

    async def run(
        self,
        solution: SolutionVersion,
        task: Task,
        *,
        exports: tuple[str, ...],
        timeout_seconds: float,
    ) -> ExportedTask:
        names = tuple(str(safe_relative(name)) for name in exports)
        if len(set(names)) != len(names) or timeout_seconds <= 0:
            raise ValueError("Exports must be unique and the task deadline positive")
        # LocalHarnessRuntime creates one workspace per invocation. A separate
        # parent here prevents concurrent task runs from harvesting each other.
        workspaces = self.root / new_id("execution") / "workspaces"
        executor = LocalHarnessRuntime(self.runtime, self.content, workspaces)
        result = await asyncio.wait_for(executor.run(solution, task), timeout_seconds)
        created = list(workspaces.iterdir())
        if len(created) != 1 or not created[0].is_dir():
            raise RuntimeError("Task runtime did not create exactly one private workspace")
        workspace = created[0]
        files: dict[str, ContentRef] = {}
        missing = []
        for name in names:
            path = workspace / name
            if any(
                (workspace / parent).is_symlink() for parent in [Path(name), *Path(name).parents]
            ):
                raise TaskExportError("Task exports must not contain symlinks")
            if not path.exists():
                missing.append(name)
            elif not path.is_file() or path.stat().st_size > 1_000_000:
                raise TaskExportError("Each task export must be a regular file of at most 1 MB")
            else:
                files[name] = await self.content.put_bytes(path.read_bytes())
        return ExportedTask(result, files, tuple(missing))


class TargetGateway:
    """Host-side model binding and request/output budget around the existing proxy.

    max_requests counts attempted upstream calls, including retries. Each call
    is capped at max_output_tokens; this bounds total generated tokens by their
    product. Input tokens and dollar spend remain measured rather than capped.
    The bearer token exposed to DSH is local and unrelated to the real API key.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        model: str,
        max_requests: int,
        max_output_tokens: int,
        timeout_seconds: float,
    ):
        if not model or min(max_requests, max_output_tokens, timeout_seconds) <= 0:
            raise ValueError("A fixed model and positive target budgets are required")
        # The compatibility proxy must not offer an unguarded localhost route
        # around the outer fixed-model/request-budget gateway. Only this host
        # closure knows its separate token; task processes receive owner.token.
        delegate_token = secrets.token_urlsafe(32)
        # The task runtime owns retries: each attempt must cross this gateway's
        # request counter. Hidden proxy retries would bypass max_requests.
        self.delegate = GatewayProxy(
            base_url, api_key, access_token=delegate_token, max_stream_attempts=1
        )
        self.token = secrets.token_urlsafe(32)
        self.calls = 0
        self.exhausted = False
        lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                if self.path not in {"/chat/completions", "/v1/chat/completions"}:
                    self.send_error(404)
                    return
                if self.headers.get("Authorization") != f"Bearer {owner.token}":
                    self.send_error(401)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 4_000_000:
                        raise ValueError("Invalid request size")
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict):
                        raise ValueError("Request must be an object")
                    if body.get("model") != model:
                        self.send_error(400, "The task target model is fixed")
                        return
                    with lock:
                        if owner.calls >= max_requests:
                            owner.exhausted = True
                            self.send_error(429, "Task model request budget exhausted")
                            return
                        owner.calls += 1
                    body["max_tokens"] = min(
                        int(body.get("max_tokens", max_output_tokens)), max_output_tokens
                    )
                    body["n"] = 1
                    if body["max_tokens"] < 1:
                        raise ValueError("Output budget must be positive")
                    if "max_completion_tokens" in body:
                        body["max_completion_tokens"] = min(
                            int(body["max_completion_tokens"]), max_output_tokens
                        )
                    request = urllib.request.Request(
                        owner.delegate.url + "/chat/completions",
                        data=json.dumps(body).encode(),
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": "Bearer " + delegate_token,
                        },
                    )
                    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                        # Buffer one model response; preserve its SSE body and
                        # usage. The actual DSH tool loop remains unchanged.
                        payload = response.read()
                        self.send_response(response.status)
                        self.send_header(
                            "Content-Type", response.headers.get("Content-Type", "application/json")
                        )
                        self.send_header("Content-Length", str(len(payload)))
                        self.end_headers()
                        self.wfile.write(payload)
                except (ValueError, KeyError, urllib.error.URLError, TimeoutError):
                    self.send_error(502, "Target request failed")

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.delegate.close()
