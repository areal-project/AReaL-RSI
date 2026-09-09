from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from areal_rsi.execution.diagnostics import failure_summary
from areal_rsi.execution.pinned_sources import mounts


def test_public_diagnostic_is_bounded_and_ignores_private_fields(tmp_path: Path) -> None:
    file = tmp_path / "diagnostic.json"
    assert failure_summary(tmp_path, "failed") == "failed"
    file.write_text(
        json.dumps(
            {
                "code": "SOURCE_DRIFT",
                "message": "固定副本不可用",
                "action": "恢复副本后重试",
                "private_log": "SECRET",
            }
        )
    )
    assert failure_summary(tmp_path, "failed") == "SOURCE_DRIFT · 固定副本不可用 · 恢复副本后重试"
    for data in ["{", "[]", " " * 16001]:
        file.write_text(data)
        assert failure_summary(tmp_path, "failed") == "failed"


def test_pinned_copy_survives_shared_edits_but_rejects_tampering(tmp_path: Path) -> None:
    root = tmp_path / "shared"
    root.mkdir()
    shared = root / "runner.py"
    shared.write_text("shared update")
    pinned = tmp_path / "frozen.py"
    pinned.write_text("frozen executable")
    config: dict[str, Any] = {
        "root": str(root),
        "files": {
            "runner.py": {
                "path": str(pinned),
                "sha256": hashlib.sha256(pinned.read_bytes()).hexdigest(),
            }
        },
    }
    assert mounts(config) == [(pinned, shared)]
    assert shared.read_text() == "shared update"
    pinned.write_text("changed frozen copy")
    with pytest.raises(ValueError, match="checksum"):
        mounts(config)
    config["files"] = {"../runner.py": next(iter(config["files"].values()))}
    with pytest.raises(ValueError, match="escapes"):
        mounts(config)
