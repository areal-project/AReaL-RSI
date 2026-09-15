from __future__ import annotations

import asyncio
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from areal_rsi.api.base import AgentSpec, ModelVersion
from areal_rsi.base.storage import FileContentStore, FileTrajectoryStore, now
from areal_rsi.modules.runtime_package import PackageAgentRuntime
from areal_rsi.modules.runtime_storage import read_journal
from areal_rsi.modules.task_execution import TargetGateway, create_task_runtime


def test_task_factory_uses_package_and_durable_journal(tmp_path: Path) -> None:
    async def scenario() -> None:
        content = FileContentStore(tmp_path / "content")
        traces = FileTrajectoryStore(tmp_path / "trajectories", content)
        runtime = create_task_runtime(
            tmp_path / "sessions",
            content,
            traces,
            runtime_config={"default": {"kind": "fake"}},
            base_url="http://127.0.0.1:1",
            api_key="fixture",
        )
        assert isinstance(runtime, PackageAgentRuntime)
        model = ModelVersion("fixture", "fixture", await content.put_text("model"), now())
        session = await runtime.open(AgentSpec("task-agent", "areal-agent-runtime@v1", model))
        try:
            assert "fake runtime handled" in await session.send("one task")
        finally:
            trace = await session.finish()
        events, snapshots = read_journal(session.directory / "data/state")
        assert events and all(s.status == "completed" for s in snapshots.values())
        stored = await traces.get(trace.trajectory_id)
        assert stored is not None
        record = json.loads(await content.read_text(stored.content))
        assert record["events"] == [e.model_dump(mode="json", by_alias=True) for e in events]

    asyncio.run(scenario())


def test_target_gateway_bounds_chat_responses_and_anthropic() -> None:
    received: list[tuple[str, dict[str, Any]]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.path, payload))
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    gateway = TargetGateway(
        f"http://127.0.0.1:{server.server_port}/v1",
        "upstream-key",
        model="fixed",
        max_requests=3,
        max_output_tokens=256,
        timeout_seconds=5,
    )

    def request(route: str, model: str = "fixed", authenticated: bool = True) -> None:
        headers = {"Content-Type": "application/json"}
        if authenticated:
            headers["x-api-key" if route == "/messages" else "Authorization"] = (
                gateway.token if route == "/messages" else "Bearer " + gateway.token
            )
        data = {"model": model, "max_tokens": 999, "max_output_tokens": 999, "n": 3}
        with urllib.request.urlopen(
            urllib.request.Request(
                gateway.url + route, data=json.dumps(data).encode(), headers=headers
            ),
            timeout=5,
        ) as response:
            assert response.status == 200

    try:
        with pytest.raises(urllib.error.HTTPError) as wrong_model:
            request("/responses", model="other")
        assert wrong_model.value.code == 400
        with pytest.raises(urllib.error.HTTPError) as unauthorized:
            request("/messages", authenticated=False)
        assert unauthorized.value.code == 401
        for route in ["/chat/completions", "/responses", "/messages"]:
            request(route)
        for route, body in received:
            field = "max_output_tokens" if route.endswith("/responses") else "max_tokens"
            assert body[field] == 256 and body["model"] == "fixed"
        with pytest.raises(urllib.error.HTTPError) as exhausted:
            request("/responses")
        assert exhausted.value.code == 429 and len(received) == 3
    finally:
        gateway.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_kernelbench_runtime_changes_the_fixed_comparison_identity() -> None:
    import runpy

    module = runpy.run_path(str(Path(__file__).parents[1] / "examples/kernelbench/run.py"))
    identify = module["execution_protocol_id"]
    assert identify("gpu-protocol", {}) == "gpu-protocol"
    dsh = identify("gpu-protocol", {"task_runtime": {"default": {"kind": "dsh"}}})
    codex = identify("gpu-protocol", {"task_runtime": {"default": {"kind": "codex-cli"}}})
    assert dsh != codex and len(dsh) == 64
