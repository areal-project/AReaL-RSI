from __future__ import annotations

import json
from pathlib import Path

import pytest
from areal_rsi_tir.assets import (
    attest_worker_inputs,
    source_files_digest,
    tir_verifier_files,
)
from areal_rsi_tir.harness import freeze_tir_harness, load_tir_harness
from areal_rsi_tir.tasks import load_tir_task_set, public_tir_tasks

from areal_rsi.api.records import RsiSplit


def test_public_tasks_exclude_answers_and_detect_tampering(
    frozen_inputs: tuple[Path, Path, Path, Path],
) -> None:
    task_root, _, _, _ = frozen_inputs
    version = load_tir_task_set(task_root)
    public = public_tir_tasks(task_root, RsiSplit.HARNESS_EVOLVE)
    assert len(public) == 1
    assert "answer" not in public[0].input
    assert (
        len({task_id for split in RsiSplit for task_id in getattr(version, split.value).task_ids})
        == 5
    )
    path = task_root / "public/harness_evolve.jsonl"
    row = json.loads(path.read_text())
    row["answer"] = "private"
    path.write_text(json.dumps(row) + "\n")
    with pytest.raises(ValueError, match="public split changed"):
        load_tir_task_set(task_root)


def test_harness_snapshot_rejects_prompt_or_runner_change(
    frozen_inputs: tuple[Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _, h0_root, runner, _ = frozen_inputs
    h0, prompt = load_tir_harness(h0_root, runner)
    assert h0.enabled_tools == ("calculator",)
    candidate = tmp_path / "candidate.txt"
    candidate.write_text(prompt.replace("Solve", "Reason"))
    h1_root = tmp_path / "h1"
    h1 = freeze_tir_harness(candidate, runner, h1_root)
    assert h1.harness_id != h0.harness_id
    (runner / "tir_workflow.py").write_text("# changed\n")
    with pytest.raises(ValueError, match="runner source changed"):
        load_tir_harness(h0_root, runner)


def test_worker_attestation_rejects_different_source(
    frozen_inputs: tuple[Path, Path, Path, Path],
) -> None:
    task_root, h0_root, runner, _ = frozen_inputs
    task_set = load_tir_task_set(task_root)
    harness, prompt = load_tir_harness(h0_root, runner)
    verifier_digest = source_files_digest(tir_verifier_files(runner))
    common = {
        "task_set_id": task_set.task_set_id,
        "harness_id": harness.harness_id,
        "runner_digest": harness.runner_digest,
        "prompt_template": prompt,
    }
    attest_worker_inputs(task_root, h0_root, runner, verifier_digest=verifier_digest, **common)
    with pytest.raises(ValueError, match="worker TIR source"):
        attest_worker_inputs(
            task_root,
            h0_root,
            runner,
            verifier_digest="sha256:" + "0" * 64,
            **common,
        )
