"""Public progress is optional and must not become a source of trusted scores."""

import json
from pathlib import Path

import pytest

from areal_rsi.execution.progress import MAX_PROGRESS_BYTES, public_progress, read_progress


def test_progress_projects_only_public_fields(tmp_path: Path) -> None:
    payload = {
        "stage": "运行验证",
        "completed": 2,
        "total": 8,
        "unit": "题",
        "message": "6 题仍在执行",
        "updated_at": "2026-09-09T10:00:00Z",
        "items": [{"id": "case1", "status": "running", "message": "调试中", "env": "SECRET"}],
        "score": 0.99,
        "passed": True,
        "private_log": "SECRET",
    }
    (tmp_path / "progress.json").write_text(json.dumps(payload))
    progress = read_progress(tmp_path)
    assert progress is not None and progress["completed"] == 2
    assert progress["items"] == [{"id": "case1", "status": "running", "message": "调试中"}]
    assert "SECRET" not in json.dumps(progress)
    assert "score" not in progress and "passed" not in progress


@pytest.mark.parametrize(
    "counts",
    [
        (-1, 8),
        (9, 8),
        (0, 0),
        (True, 8),
        (0, False),
        (float("nan"), 8),
        (1, float("inf")),
        (10**400, 10**401),
        ("2", 8),
        (None, 8),
    ],
)
def test_invalid_counts_do_not_produce_a_percentage(counts: tuple[object, object]) -> None:
    assert public_progress({"stage": "进行中", "completed": counts[0], "total": counts[1]}) == {
        "stage": "进行中"
    }


def test_absent_partial_oversized_and_linked_files_do_not_break_polling(tmp_path: Path) -> None:
    path = tmp_path / "progress.json"
    assert read_progress(tmp_path) is None
    for raw in ['{"completed":', "[]", "{}", "[" * 2000, " " * (MAX_PROGRESS_BYTES + 1)]:
        path.write_text(raw)
        assert read_progress(tmp_path) is None
    path.unlink()
    private = tmp_path / "private.json"
    private.write_text('{"message":"private"}')
    path.symlink_to(private)
    assert read_progress(tmp_path) is None
