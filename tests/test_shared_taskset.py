"""Harness generation and selection share training; evaluation stays disjoint."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from areal_rsi_tir.tasks import (
    TirSplitSizes,
    freeze_shared_tir_task_set,
    freeze_tir_task_set,
    load_tir_task_set,
)

from areal_rsi.api.records import RsiSplit


def test_shared_training_pool_is_frozen_before_search(tmp_path: Path) -> None:
    train_source = tmp_path / "train.jsonl"
    train_source.write_text(
        "".join(
            json.dumps(
                {
                    "messages": [{"role": "user", "content": f"Solve {i}+7."}],
                    "answer": str(i + 7),
                }
            )
            + "\n"
            for i in range(32)
        )
    )
    test_source = tmp_path / "test.jsonl"
    test_source.write_text(
        json.dumps({"messages": [{"role": "user", "content": "Solve 99+7."}], "answer": "106"})
        + "\n"
    )
    parent_root, shared_root = tmp_path / "parent", tmp_path / "shared"
    parent = freeze_tir_task_set(
        train_source,
        test_source,
        parent_root,
        sizes=TirSplitSizes(2, 2, 24, 4),
        seed=7,
    )
    shared = freeze_shared_tir_task_set(parent_root, shared_root, seed=11, train_count=8)
    assert len(shared.model_train.task_ids) == 8
    assert shared.harness_evolve == shared.model_train
    assert shared.harness_select == shared.model_train
    assert shared.model_dev == parent.model_dev
    assert shared.sealed_test == parent.sealed_test
    assert set(shared.model_train.task_ids).isdisjoint(shared.model_dev.task_ids)
    assert load_tir_task_set(shared_root) == shared
    for evaluation in (shared.model_dev, shared.sealed_test):
        with pytest.raises(ValueError, match="overlap across protected"):
            replace(shared, harness_select=evaluation)
    assert (shared_root / "public/harness_select.jsonl").read_bytes() == (
        shared_root / "public/model_train.jsonl"
    ).read_bytes()
    provenance = shared_root / "shared-provenance.json"
    provenance.write_bytes(provenance.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="Shared task set provenance changed"):
        load_tir_task_set(shared_root)
    assert (
        len(
            {
                task_id
                for split in (RsiSplit.MODEL_DEV, RsiSplit.SEALED_TEST)
                for task_id in getattr(shared, split.value).task_ids
            }
        )
        == 5
    )
