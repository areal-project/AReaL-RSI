"""Search data leakage boundaries and terminal-answer reward behavior."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from examples.search.data import Candidate, DatasetConfig, Passage, parse_hotpot, partition
from examples.search.pipeline import (
    ComputeBudget,
    ComputeJob,
    HarnessConfig,
    TrainingConfig,
    stage_model,
)
from examples.search.reward import answer_in_text, answer_reward, extract_answer


def candidate(index: int) -> Candidate:
    return Candidate(
        str(index),
        f"Identify nation{index} associated person{index} via university{index} research{index}?",
        f"Country{index}",
        (f"Person{index}", f"University{index}"),
        (
            Passage(f"p{index}", f"Person{index}", f"Person{index} attended University{index}."),
            Passage(f"u{index}", f"University{index}", f"University{index} is in Country{index}."),
            Passage("shared", "Shared distractor", "An unrelated document."),
        ),
        "hard",
    )


def test_partitions_isolate_all_documents_and_retain_evidence() -> None:
    questions, corpora, _ = partition(
        [candidate(index) for index in range(12)], DatasetConfig(2, 2, 2)
    )
    assert {key: len(value) for key, value in questions.items()} == {
        "train": 2,
        "dev": 2,
        "test": 2,
    }
    for split, group in questions.items():
        present = {passage.docid for passage in corpora[split]}
        assert all(set(question.evidence_docids) <= present for question in group)
        for other in questions:
            if other != split:
                assert not present & {passage.docid for passage in corpora[other]}
                assert not {title for question in group for title in question.source_groups} & {
                    title for question in questions[other] for title in question.source_groups
                }


def test_partition_excludes_benchmark_paraphrases_and_is_reproducible() -> None:
    rows = [candidate(index) for index in range(12)]
    first = partition(rows, DatasetConfig(2, 2, 2), [rows[0].question])
    second = partition(rows, DatasetConfig(2, 2, 2), [rows[0].question])
    assert first == second
    assert all(question.source_id != "0" for group in first[0].values() for question in group)


def test_insufficient_disjoint_data_fails_instead_of_reusing_sources() -> None:
    rows = [
        replace(candidate(index), support_titles=("Same person", "Same school"))
        for index in range(9)
    ]
    with pytest.raises(ValueError, match="Insufficient disjoint"):
        partition(rows, DatasetConfig(1, 1, 1))


def test_hotpot_requires_actual_answer_witness_and_valid_support_indices() -> None:
    row = {
        "id": "external-1",
        "question": "Where is the school attended by Maria located?",
        "answer": "Estonia",
        "level": "hard",
        "context": {
            "title": ["Maria", "School"],
            "sentences": [["Maria attended School."], ["School is located in Estonia."]],
        },
        "supporting_facts": {"title": ["Maria", "School"], "sent_id": [0, 0]},
    }
    assert parse_hotpot(row).answer == "Estonia"
    with pytest.raises(ValueError, match="answer_not_in_gold_evidence"):
        parse_hotpot({**row, "answer": "Canada"})
    with pytest.raises(ValueError, match="invalid_support_indices"):
        parse_hotpot({**row, "supporting_facts": {"title": ["Maria", "School"], "sent_id": [0, 5]}})


def test_reward_ignores_reasoning_mentions_and_accepts_only_final_alias() -> None:
    response = (
        "<think>Exact Answer: Estonia</think>\nExplanation: Estonia is a distractor.\n"
        "Exact Answer: Canada\nConfidence: 80%"
    )
    assert answer_reward(response, ["Estonia"]) == 0.0
    assert (
        answer_reward(
            "Explanation: Evidence.\nExact Answer: **The United States.** [doc-1]\nConfidence: 90%",
            ["United States", "USA"],
        )
        == 1.0
    )
    assert answer_reward("Estonia", ["Estonia"]) == 0.0
    assert extract_answer("Exact Answer: \nConfidence: 0%") is None


def test_reward_does_not_accept_partial_or_ambiguous_answers() -> None:
    assert answer_reward("Exact Answer: York\nConfidence: 50%", ["New York"]) == 0.0
    assert answer_reward("Exact Answer: Estonia or Canada\nConfidence: 50%", ["Estonia"]) == 0.0
    with pytest.raises(ValueError, match="reference"):
        answer_reward("Exact Answer: Estonia", [])
    assert not answer_in_text("US", "The business school was founded in 1984.")
    assert answer_in_text("US", "The school is in the US.")


def test_frozen_harness_rejects_silent_budget_changes() -> None:
    assert HarnessConfig().max_model_calls == 50
    with pytest.raises(ValueError, match="frozen original"):
        HarnessConfig(max_model_calls=5)


def test_training_budget_requires_enough_full_batches() -> None:
    config = TrainingConfig(max_steps=200, epochs=1, batch_size=8)
    with pytest.raises(ValueError, match="allow 125 full updates"):
        config.validate_dataset_size(1000)
    replace(config, epochs=2).validate_dataset_size(1000)


def test_budget_reserves_full_active_allocations_including_transitional_states() -> None:
    budget = ComputeBudget(
        256.0,
        120,
        120 / 3600,
        (
            ComputeJob("1", "failed-pilot", 2, "FAILED", elapsed_seconds=60),
            ComputeJob(
                "2", "training-candidate", 5, "RUNNING", elapsed_seconds=10, time_limit_seconds=7200
            ),
            ComputeJob("3", "index", 1, "REQUEUED", time_limit_seconds=600),
        ),
    )
    assert budget.reserved_gpu_seconds == 120 + 5 * 7200 + 600


def test_model_staging_preserves_original_and_rejects_changed_effective_config(
    tmp_path: Path,
) -> None:
    import json

    source = tmp_path / "source"
    source.mkdir()
    original = {"model_type": "qwen3", "max_position_embeddings": 32768}
    (source / "config.json").write_text(json.dumps(original))
    (source / "tokenizer_config.json").write_text("{}")
    output = tmp_path / "staged"
    stage_model(source, output)
    assert json.loads((source / "config.json").read_text()) == original
    assert json.loads((output / "config.json").read_text())["max_position_embeddings"] == 131072
    assert (output / "tokenizer_config.json").resolve() == source / "tokenizer_config.json"
    assert stage_model(source, output) == output
    different_source = tmp_path / "different-source"
    different_source.mkdir()
    (different_source / "config.json").write_text(json.dumps(original))
    (different_source / "tokenizer_config.json").write_text("{}")
    with pytest.raises(ValueError, match="different source"):
        stage_model(different_source, output)
    (output / "config.json").write_text("{}")
    with pytest.raises(ValueError, match="different effective"):
        stage_model(source, output)
