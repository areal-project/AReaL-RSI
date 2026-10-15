from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_missing_model_configuration_has_actionable_error(tmp_path: Path) -> None:
    env = {key: value for key, value in os.environ.items() if not key.startswith("RSI_")}
    env["HOME"] = str(tmp_path)
    env["RSI_DATA_ROOT"] = str(tmp_path / "run")
    result = subprocess.run(
        [sys.executable, "-m", "areal_rsi", "tui"],
        env=env,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "RSI_TEACHER_MODEL" in result.stderr
    assert "config.json" in result.stderr
    assert "Traceback" not in result.stderr
