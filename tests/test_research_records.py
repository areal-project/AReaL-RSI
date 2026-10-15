from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from areal_rsi.api.records import RecordRef, ResearchPlan, ResearchRun, RunState
from areal_rsi.api.research import (
    ResearchLimits,
    TaskBrief,
    VerificationContract,
    VerifiedResult,
)
from areal_rsi.infra.coordination.store import ResearchStore
from areal_rsi.infra.storage.files import now


def make_store(root: Path, name: str = "example") -> ResearchStore:
    store = ResearchStore(root, name)
    brief = TaskBrief(
        "Improve the result",
        "Keep the solution general",
        ResearchLimits(allowed_paths=("skills/*",)),
        VerificationContract("accuracy", target=0.9),
    )
    brief_ref = store.put("briefs", "initial", brief)
    store.save_run(
        ResearchRun(
            name,
            ResearchPlan(brief.objective, 10),
            RunState.PLANNED,
            brief_ref=brief_ref,
        )
    )
    return store


def test_selection_requires_matching_trusted_comparison(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = make_store(tmp_path)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        candidate = await store.snapshot(workspace, agent_id="operator", description="Seed")
        result = VerifiedResult(
            "verify1",
            candidate.candidate_id,
            store.contract_digest(),
            now(),
            "succeeded",
            0.6,
            True,
        )
        store.record_verification(result)
        assert store.consider_best(result.result_id)
        assert store.run().baseline_result_ref is None
        for identifier, changes in (
            ("lower", {"score": 0.5}),
            ("diagnostic", {"score": 0.9, "phase": "diagnostic"}),
            ("simulated", {"score": 1.0, "simulated": True}),
            ("incorrect", {"score": 1.0, "passed": False}),
        ):
            item = replace(result, result_id=identifier, **changes)
            store.record_verification(item)
            assert not store.consider_best(item.result_id)
        with pytest.raises(ValueError, match="different comparison"):
            store.record_verification(replace(result, result_id="wrong", contract_digest="other"))
        assert store.public_record(store.run().best_result_ref)["score"] == 0.6  # type: ignore[arg-type]

    asyncio.run(scenario())


def test_short_record_ids_resolve_exactly_within_public_records_of_this_run(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    ref = store.put("materials", "feedback_exact", {"value": 42})
    other = make_store(tmp_path, "another_run")
    foreign = other.put("materials", "foreign_only", {"value": 43})
    private = store.put("coordination", "secret", {"value": 44})
    assert store.record_reference(RecordRef("feedback_exact")) == ref
    assert store.record_reference(RecordRef("feedback_exact.json")) == ref
    assert store.record_reference(RecordRef("materials/feedback_exact")) == ref
    assert store.record_reference(RecordRef("materials/feedback_exact.json")) == ref
    assert store.record_reference(ref) == ref
    for value in ("feedback_exac", "foreign_only", "secret"):
        with pytest.raises(ValueError, match="不存在或不唯一"):
            store.record_reference(RecordRef(value))
    for value in (foreign.uri, private.uri):
        with pytest.raises(ValueError, match="outside"):
            store.record_reference(RecordRef(value))
    for value in (
        "../materials/feedback_exact", "/tmp/feedback_exact", "coordination/secret",
        "another_run/materials/foreign_only", "materials/foreign_only",
        "materials/../feedback_exact", "materials//feedback_exact", "materials/feedback_exac",
        "materials/feedback_exact?run=another_run", "materials%2Ffeedback_exact",
    ):
        with pytest.raises(ValueError):
            store.record_reference(RecordRef(value))
    store.put("reports", "feedback_exact", {"value": 45})
    with pytest.raises(ValueError, match="不存在或不唯一"):
        store.record_reference(RecordRef("feedback_exact"))
    assert store.record_reference(ref) == ref
    assert store.record_reference(RecordRef("materials/feedback_exact")) == ref
