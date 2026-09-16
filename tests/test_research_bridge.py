from __future__ import annotations

import asyncio
import json
import sys
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent

from areal_rsi.api.research import TaskBrief
from areal_rsi.coordination.config import ResearchConfig
from areal_rsi.execution import ApprovedCommand
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
    config = ResearchConfig(TaskBrief("Generic research", "Improve the policy"), seed_dir=str(seed))
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
    monkeypatch.setenv("RSI_RESEARCH_CONFIG", str(tmp_path / "missing.json"))
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


def test_persona_and_environment_route_configured_research() -> None:
    from areal_rsi.modules.dsh_config import MCP_ENVIRONMENT, PERSONA

    assert "rsi_research_start" in PERSONA
    assert "rsi_research_status" in PERSONA
    assert "RSI_RESEARCH_CONFIG" in MCP_ENVIRONMENT
    assert "RSI_RESEARCH_PORT" in MCP_ENVIRONMENT


@pytest.mark.parametrize("failure", [None, "identity", "command", "malformed", "credentials"])
def test_preflight_runs_operator_command_and_checks_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    identity = {"protocol_id": "fixture"}
    script = tmp_path / "check.py"
    script.write_text(
        "import json,os,sys\n"
        "assert os.environ['FIXTURE_SETTING'] == 'visible'\n"
        "print(json.dumps({'verification_identity': {'protocol_id': 'fixture'}}))\n"
    )
    config = ResearchConfig(
        TaskBrief("Generic research", ""),
        preflight=ApprovedCommand("preflight", (sys.executable, str(script)), timeout_seconds=5),
        metadata={"verification_identity": identity},
        execution_env={"FIXTURE_SETTING": "visible"},
    )
    monkeypatch.setenv("RSI_API_KEY", "fixture")
    if failure == "identity":
        config.metadata["verification_identity"] = {"protocol_id": "other"}
    if failure == "command":
        script.write_text("raise RuntimeError('SECRET')")
    if failure == "malformed":
        script.write_text("print('SECRET')")
    if failure == "credentials":
        monkeypatch.delenv("RSI_API_KEY")
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config.public()))
    monkeypatch.setenv("RSI_RESEARCH_CONFIG", str(path))
    if failure:
        with pytest.raises(ValueError, match=r"不一致|凭证|预检失败") as error:
            bridge.prepared_config()
        assert "SECRET" not in str(error.value)
    else:
        assert bridge.prepared_config().metadata["verification_identity"] == identity


def test_infra_does_not_import_benchmark_implementation() -> None:
    root = Path(__file__).parents[1]
    assert not (root / "src/areal_rsi/adapters/past_bench.py").exists()
    for file in (
        "ui/research_bridge.py",
        "ui/mcp.py",
        "modules/dsh_config.py",
        "resources/dsh/persona.md",
    ):
        source = (root / "src/areal_rsi" / file).read_text().lower()
        assert "past_bench" not in source
        assert "past-bench" not in source


@pytest.mark.parametrize(
    "change",
    [
        {"parameters": ("workspace",)},
        {"gpu_count": 1},
        {"auto_verify_output": "candidate"},
        {"timeout_seconds": None},
    ],
)
def test_preflight_is_bounded_and_cannot_accept_agent_parameters(change: dict[str, Any]) -> None:
    from areal_rsi.coordination.intake import validate_execution

    command = ApprovedCommand("preflight", (sys.executable, "-V"), timeout_seconds=5)
    config = ResearchConfig(TaskBrief("Check", ""), preflight=replace(command, **change))
    assert any("预检命令" in issue for issue in validate_execution(config))
