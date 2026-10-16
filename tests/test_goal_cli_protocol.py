"""The automation protocol must expose input errors on its NDJSON stdout."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("failure", ["invalid_spec", "invalid_machine_config"])
def test_json_submission_errors_have_identity_fields_and_exit_two(
    tmp_path: Path, failure: str
) -> None:
    spec = tmp_path / "goal.json"
    spec.write_text("{}" if failure == "invalid_machine_config" else "{invalid json")
    if failure == "invalid_machine_config":
        config = tmp_path / ".areal_rsi/config.json"
        config.parent.mkdir()
        config.write_text("{invalid json")
    env = {key: value for key, value in os.environ.items() if not key.startswith("RSI_")}
    env.update(HOME=str(tmp_path), RSI_DATA_ROOT=str(tmp_path / "data"))
    result = subprocess.run(
        [sys.executable, "-m", "areal_rsi", "exec", "--spec", str(spec), "--yes", "--json"],
        env=env,
        input="",
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 2
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(events) == 1
    assert events[0]["schema_version"] == 1 and events[0]["type"] == "error"
    assert {"goal_id", "revision", "run_id"} <= events[0].keys()
    assert events[0]["error"] and "Traceback" not in result.stderr
