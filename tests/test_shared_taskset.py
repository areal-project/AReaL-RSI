"""Harness evolution shares training; Harness selection shares development."""

from __future__ import annotations

import json
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
    assert shared.harness_select == shared.model_dev
    assert shared.model_dev == parent.model_dev
    assert shared.sealed_test == parent.sealed_test
    assert set(shared.model_train.task_ids).isdisjoint(shared.model_dev.task_ids)
    assert load_tir_task_set(shared_root) == shared
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
