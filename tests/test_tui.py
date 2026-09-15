from pathlib import Path
from typing import Any

import pytest

from areal_rsi.ui.tui import TerminalClient, wrapped


class Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.fail = False

    def request(self, path: str, body: Any = None) -> dict[str, Any]:
        self.calls.append((path, body))
        if self.fail:
            self.fail = False
            raise OSError("connection lost")
        return {
            "run_id": "research1",
            "status": "running",
            "events": [
                {"sequence": 5, "kind": "message", "data": {"sender": "rsi", "text": "真实状态"}}
            ],
        }


def test_controls_and_chat_use_the_same_run_and_retry_identity() -> None:
    client = Client()
    terminal = TerminalClient(client)  # type: ignore[arg-type]
    terminal.select("research1")
    terminal.submit("/start")
    assert client.calls[-1] == ("/api/runs/research1/start", {})
    client.fail = True
    with pytest.raises(OSError):
        terminal.submit("继续研究")
    failed: tuple[str, Any] = client.calls[-1]
    terminal.submit("/retry-send")
    assert client.calls[-1] == failed
    terminal.poll()
    assert terminal.after == 5
    assert terminal.lines[-1] == "[rsi] 真实状态"
    assert terminal.submit("/quit") is False
    assert not any(path.endswith("/stop") for path, _ in client.calls)


def test_config_creation_transfers_credentials_to_server_not_terminal_output(
    tmp_path: Path,
) -> None:
    from areal_rsi.api.research import TaskBrief
    from areal_rsi.base.storage import write_json
    from areal_rsi.coordination.config import ResearchConfig

    config = ResearchConfig(TaskBrief("test", "instructions"), api_key_env="UNSET_TEST_KEY")
    path = tmp_path / "research.json"
    write_json(path, config.public())
    client = Client()
    terminal = TerminalClient(client)  # type: ignore[arg-type]
    terminal.create(path)
    assert client.calls[0][1]["config"]["brief"]["objective"] == "test"
    assert "model_settings" in client.calls[0][1]


def test_chinese_lines_use_terminal_cell_width() -> None:
    assert wrapped("数学abc", 4) == ["数学", "abc"]


def test_terminal_create_uses_real_http_contract(tmp_path: Path) -> None:
    import threading

    from areal_rsi.research_app import ResearchApp
    from areal_rsi.ui.launch import WorkbenchClient
    from areal_rsi.ui.workbench import WorkbenchServer

    app = ResearchApp(tmp_path)
    server = WorkbenchServer(app, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = WorkbenchClient(server.server_port)
        client.bootstrap()
        terminal = TerminalClient(client)
        terminal.create()
        terminal.poll()
        assert terminal.run_id
        assert "planned" in terminal.status
        assert terminal.submit("/quit") is False
        assert client.request("/api/runs")["runs"][0]["run_id"] == terminal.run_id
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
