"""OPD experience_path resolution: explicit config wins; else derive from skill."""

import json

from areal_rsi.controller.local_controller import _with_opd_experience_path


def test_configured_experience_path_wins(tmp_path):
    out = tmp_path / "derived.json"
    skill = {"spec": {"task_experiences": [{"task_id": "t1", "experience": "hint"}]}}
    result = _with_opd_experience_path(
        {"experience_path": "/fixed/experience.json", "train_data_path": "/d.jsonl"},
        skill,
        out,
    )
    assert result["experience_path"] == "/fixed/experience.json"
    assert not out.exists()  # researcher experiences not materialized


def test_derives_experience_path_from_skill_when_unset(tmp_path):
    out = tmp_path / "output" / "experience_opd.json"
    skill = {"spec": {"task_experiences": [
        {"task_id": "gsm8k-train-0", "experience": "hint A"},
        {"task_id": "gsm8k-train-1", "experience": "hint B"},
    ]}}
    result = _with_opd_experience_path({"train_data_path": "/d.jsonl"}, skill, out)
    assert result["experience_path"] == str(out)
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written == {"gsm8k-train-0": "hint A", "gsm8k-train-1": "hint B"}
