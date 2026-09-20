"""The same command boundary protects local clients and the HTTP workbench."""

from __future__ import annotations

import asyncio
import threading
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from areal_rsi.api.control import ArtifactDownload
from areal_rsi.api.research import TaskBrief
from areal_rsi.infra.coordination.config import ResearchConfig
from areal_rsi.infra.coordination.coordinator import Coordinator
from areal_rsi.infra.research import ResearchSystem
from areal_rsi.infra.storage.files import write_json
from areal_rsi.ui.launch import WorkbenchClient
from areal_rsi.ui.workbench import WorkbenchServer


@pytest.mark.parametrize("action,params", [
    ("misspelled-action", {}),
    ("create", {"simulated": "false"}),
    ("create", {"unknown": "private-sentinel"}),
    ("retry", {"run_id": "missing"}),
    ("detail", {"run_id": "missing", "event_limit": 1001}),
])
def test_invalid_requests_are_rejected_before_lookup_or_mutation(
    tmp_path: Path, action: str, params: dict[str, Any],
) -> None:
    root = tmp_path / "application"
    with pytest.raises(ValueError) as error:
        asyncio.run(ResearchSystem(root).call(action, params))
    assert "private-sentinel" not in str(error.value)
    assert not root.exists()


def test_http_rejects_unknown_fields_and_serves_transport_neutral_download(tmp_path: Path) -> None:
    artifact = tmp_path / "candidate.zip"
    artifact.write_bytes(b"exported candidate")

    class Backend:
        def __init__(self) -> None:
            self.commands: list[tuple[str, dict[str, Any]]] = []

        async def call(
            self, action: str, params: dict[str, Any]
        ) -> dict[str, Any] | ArtifactDownload:
            self.commands.append((action, params))
            if action == "export":
                return ArtifactDownload(artifact, "candidate.zip")
            return {}

    backend = Backend()
    server = WorkbenchServer(backend, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = WorkbenchClient(server.server_port)
        client.token = client.request("/api/bootstrap")["csrf_token"]
        for route, payload in (
            ("/api/runs/run_1/pause", {"typo": "private-sentinel"}),
            ("/api/runs/run_1/pause", {"run_id": "another"}),
            ("/api/runs", {"simulated": True}),
        ):
            with pytest.raises(ValueError):
                client.request(route, payload)
        assert backend.commands == [("bootstrap", {})]
        request = urllib.request.Request(
            server.url + "/api/runs/run_1/export", data=b'{"candidate_id":"candidate_1"}',
            headers={"Content-Type": "application/json", "X-RSI-Token": client.token},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            assert response.read() == artifact.read_bytes()
            assert "candidate.zip" in response.headers["Content-Disposition"]
        assert backend.commands[-1] == (
            "export", {"run_id": "run_1", "candidate_id": "candidate_1"}
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_direct_stop_and_dispatch_share_operator_reason(tmp_path: Path) -> None:
    async def scenario() -> None:
        system = ResearchSystem(tmp_path)
        config = ResearchConfig(TaskBrief("Research", "Inspect contracts"))
        for run_id in ("direct", "dispatch"):
            c = Coordinator.create(tmp_path, config.brief, run_id=run_id)
            write_json(c.store.root / "coordination/config.json", config.public())
        await system.stop("direct", reason="operator reason")
        await system.call("stop", {"run_id": "dispatch", "reason": "operator reason"})
        for run_id in ("direct", "dispatch"):
            c = system.coordinator(run_id)
            assert c.state["status"] == "stopped"
            assert any(event.data.get("reason") == "operator reason" for event in c.store.events())
        await system.close()

    asyncio.run(scenario())
