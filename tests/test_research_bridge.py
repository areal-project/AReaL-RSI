from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent

from areal_rsi.api.research import TaskBrief
from areal_rsi.coordination.config import ResearchConfig
from areal_rsi.modules.responses import ResponsesConfig, ResponsesRuntime
from areal_rsi.research_app import ResearchApp
from areal_rsi.ui import research_bridge as bridge
from areal_rsi.ui.mcp import create_server
from areal_rsi.ui.workbench import WorkbenchServer
from areal_rsi.ui.workspace import ExperimentWorkspace


class QuietTransport:
    async def request(self, config: Any, payload: Any, sink: Any) -> dict[str, Any]:
        return {
            "id": "fixture",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Waiting for evidence."}],
                }
            ],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }


def test_real_http_start_retry_pause_resume_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "prompt.md").write_text("A generic policy")
    config = ResearchConfig(TaskBrief("PAST-Bench", "Improve the policy"), seed_dir=str(seed))
    monkeypatch.setattr(bridge, "prepared_config", lambda: config)
    controller = bridge.ResearchBridge(tmp_path)
    app = ResearchApp(
        controller.root,
        runtime_factory=lambda _: ResponsesRuntime(
            ResponsesConfig(base_url="https://example.test", api_key="fixture"), QuietTransport()
        ),
    )
    server = WorkbenchServer(app, port=0)
    controller.port = server.server_port
    monkeypatch.setenv("RSI_RESEARCH_PORT", str(server.server_port))
    mcp = create_server(ExperimentWorkspace(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        response = asyncio.run(
            mcp.call_tool(
                "rsi_research_start",
                {
                    "goal": "优化 PAST-Bench",
                    "request_id": "request_one",
                },
            )
        )
        assert isinstance(response, CallToolResult) and not response.is_error
        assert isinstance(response.content[0], TextContent)
        first = json.loads(response.content[0].text)
        assert first["simulated"] is False
        assert first["status"] == "running"
        run_id = first["run_id"]
        again = controller.start("优化 PAST-Bench", "request_one")
        assert again["run_id"] == run_id
        assert len(list((controller.root / "runs").iterdir())) == 1
        with pytest.raises(ValueError, match="其他目标"):
            controller.start("different", "request_one")
        assert controller.control(run_id, "pause")["status"] == "paused"
        # Replayed start must not undo an explicit pause.
        assert controller.start("优化 PAST-Bench", "request_one")["status"] == "paused"
        assert controller.control(run_id, "resume")["status"] == "running"
        assert controller.control(run_id, "stop")["status"] == "stopped"
        assert controller.control(run_id)["status"] == "stopped"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_preflight_failure_never_starts_service_or_saves_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RSI_PAST_BENCH_CONFIG", str(tmp_path / "missing.json"))
    controller = bridge.ResearchBridge(tmp_path)

    def unexpected(*args: Any) -> Any:
        pytest.fail("must preflight before starting server")

    monkeypatch.setattr(bridge, "ensure_workbench", unexpected)
    with pytest.raises(ValueError, match="尚未配置"):
        controller.start("optimize", "one")
    assert not list(controller.root.rglob("*.json"))


def test_status_does_not_publish_private_records() -> None:
    result = bridge.summary(
        {
            "run_id": "fixture",
            "config": {"api_key": "SECRET"},
            "best": {"score": 0.6, "metrics": {"evaluation_score": 1.0}, "trace": "SECRET"},
            "jobs": [{"job_id": "one", "log": "SECRET", "error": "SECRET"}],
            "messages": ["SECRET"],
        },
        "http://127.0.0.1:8198",
    )
    assert "SECRET" not in json.dumps(result)
    assert result["best"]["score"] == 0.6


def test_persona_and_environment_route_prepared_past_bench() -> None:
    from areal_rsi.modules.dsh_config import MCP_ENVIRONMENT, PERSONA

    assert "rsi_research_start" in PERSONA
    assert "rsi_research_status" in PERSONA
    assert "RSI_PAST_BENCH_CONFIG" in MCP_ENVIRONMENT
    assert "RSI_RESEARCH_PORT" in MCP_ENVIRONMENT


@pytest.mark.parametrize("failure", [None, "identity", "credentials"])
def test_preflight_checks_protocol_and_target_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    from areal_rsi.adapters import past_bench as pb

    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "prompt.md").write_text("Generic policy")
    lock = {
        "protocol_id": "fixed",
        "protocol": {
            "options": {
                "target_model": "target",
                "api_key_env": "FIXTURE_TARGET_KEY",
                "judge_api_key_env": "FIXTURE_TARGET_KEY",
            }
        },
        "manifests": {split: [] for split in pb.PHASES.values()},
    }
    monkeypatch.setattr(pb, "load_lock", lambda _: lock)
    config = pb.research_config(tmp_path / "lock.json", seed)
    monkeypatch.setenv("RSI_TEACHER_API_KEY", "fixture")
    monkeypatch.setenv("FIXTURE_TARGET_KEY", "fixture")
    if failure == "identity":
        config.metadata["verification_identity"]["phases"]["search"]["protocol_id"] = "other"
    if failure == "credentials":
        monkeypatch.delenv("FIXTURE_TARGET_KEY")
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config.public()))
    monkeypatch.setenv("RSI_PAST_BENCH_CONFIG", str(path))
    if failure:
        with pytest.raises(ValueError, match=r"不一致|凭证"):
            bridge.prepared_config()
    else:
        assert bridge.prepared_config().metadata["template"] == "past-bench"
