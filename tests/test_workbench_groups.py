"""Workbench groups survive app restarts without changing research records."""

import asyncio
import threading
from pathlib import Path

from areal_rsi.api.research import TaskBrief
from areal_rsi.base.storage import write_json
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.research_app import ResearchApp
from areal_rsi.ui.launch import WorkbenchClient
from areal_rsi.ui.workbench import WorkbenchServer


def test_saved_groups_reach_http_and_leave_new_runs_ungrouped(tmp_path: Path) -> None:
    for run_id in ("first", "second"):
        Coordinator.create(tmp_path, TaskBrief(run_id, ""), run_id=run_id)
    before = {path: path.read_bytes() for path in (tmp_path / "runs").rglob("*.json")}
    ungrouped = asyncio.run(ResearchApp(tmp_path).call("list", {}))
    assert all(run["group"] is None for run in ungrouped["runs"])
    group = {"id": "batch", "name": "0915-153620", "created_at": "2026-09-15T07:36:20Z"}
    write_json(
        tmp_path / "workbench-groups.json",
        {"groups": [{**group, "run_ids": ["first", "deleted"], "private": "hidden"}]},
    )
    server = WorkbenchServer(ResearchApp(tmp_path), port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = WorkbenchClient(server.server_port)
        records = {run["run_id"]: run for run in client.request("/api/runs")["runs"]}
        assert set(records) == {"first", "second"}
        assert records["first"]["group"] == group
        assert records["second"]["group"] is None
        assert {path: path.read_bytes() for path in before} == before
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
