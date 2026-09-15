from __future__ import annotations

import subprocess
import sys


def test_all_entry_points_use_research_commands() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "areal_rsi", "--help"], text=True, capture_output=True, check=True
    )
    assert "{tui,workbench,research,prepare}" in result.stdout
    for removed in ("local-app", "_experiment-worker", "--plan"):
        assert removed not in result.stdout


def test_tui_can_select_a_research_config_or_existing_run() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "areal_rsi", "tui", "--help"],
        text=True,
        capture_output=True,
        check=True,
    )
    assert "--config" in result.stdout
    assert "--resume" in result.stdout
    assert "--mode" not in result.stdout
