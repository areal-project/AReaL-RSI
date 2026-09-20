"""Retrying a batch launch does not restart completed studies or duplicate its group."""

import json
import runpy
from pathlib import Path

from areal_rsi.base.storage import write_json


def test_batch_retry_preserves_stopped_studies_and_existing_groups(tmp_path, monkeypatch):
    example = Path(__file__).resolve().parents[1] / "examples/kernelbench_artifacts"
    monkeypatch.syspath_prepend(str(example))
    launch = runpy.run_path(str(example / "batch.py"))["register_and_start"]
    states = {"fresh": "planned", "finished": "stopped"}
    starts = []
    rows = []
    for name in states:
        write_json(tmp_path / "runs" / name / "coordination/collaboration.json", {})
        rows.append({"run_id": name, "collaboration": {}})
    write_json(tmp_path / "workbench-groups.json", {"groups": [{"id": "old"}]})

    class Client:
        token = "test"
        url = "http://localhost:8198"

        def __init__(self, port):
            pass

        def request(self, path, body=None):
            if path == "/api/runs":
                assert body is None  # Both studies are already registered.
                return {"runs": [{"run_id": name, "created_at": "2026-09-20"} for name in states]}
            name = path.split("/")[3]
            if path.endswith("/start"):
                starts.append(name)
                states[name] = "running"
            return {"status": states[name]}

    monkeypatch.setitem(launch.__globals__, "WorkbenchClient", Client)
    batch = {
        "data_root": str(tmp_path),
        "directory": str(tmp_path / "operator"),
        "port": 8198,
        "batch_id": "new",
        "group_name": "test",
        "runs": rows,
    }
    launch(batch, Client(8198))
    launch(batch, Client(8198))
    assert starts == ["fresh"]
    assert states["finished"] == "stopped"
    groups = json.loads((tmp_path / "workbench-groups.json").read_text())["groups"]
    assert [g["id"] for g in groups] == ["new", "old"]
