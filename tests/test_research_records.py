from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from areal_rsi.api.base import ExperimentPlan, ExperimentRun, RecordRef, RunState
from areal_rsi.api.research import (
    ResearchLimits,
    ResearchReport,
    TaskBrief,
    VerificationContract,
    VerifiedResult,
)
from areal_rsi.base.storage import now
from areal_rsi.coordination.store import ResearchStore


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
        ExperimentRun(
            name,
            ExperimentPlan(brief.objective, "research", "", None, None, 10),
            RunState.PLANNED,
            brief_ref=brief_ref,
        )
    )
    return store


def test_optional_products_and_natural_instructions(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    run = store.run()
    assert run.best_result_ref is run.baseline_result_ref is None
    assert run.plan.target is None
    assert store.brief().instructions == "Keep the solution general"
    report = ResearchReport(
        "report1",
        "node1",
        "task1",
        "Read the environment documentation and confirmed the command interface. "
        "No GPU is available, so execution was not tested. Provision the configured runner.",
        now(),
    )
    ref = store.put("reports", report.report_id, report)
    assert "confirmed the command interface" in store.public_record(ref)["message"]
    assert not (store.root / "candidates").exists()


def test_candidate_scope_compares_actual_content(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = make_store(tmp_path)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "prompt.md").write_text("Fixed prompt")
        (workspace / "skills").mkdir()
        (workspace / "skills" / "kernel.md").write_text("Original skill")
        seed = await store.snapshot(workspace, agent_id="operator", description="Seed")
        (workspace / "skills" / "kernel.md").write_text("Improved skill")
        candidate = await store.snapshot(
            workspace,
            agent_id="ra",
            description="Update skill",
            parent_candidate_id=seed.candidate_id,
        )
        (workspace / "prompt.md").write_text("Disallowed change")
        with pytest.raises(ValueError, match=r"outside.*prompt\.md"):
            await store.snapshot(
                workspace,
                agent_id="ra",
                description="Edit prompt",
                parent_candidate_id=candidate.candidate_id,
            )
        exported = await store.export(candidate.candidate_id, tmp_path / "export")
        assert (exported / "prompt.md").read_text() == "Fixed prompt"
        assert (exported / "skills" / "kernel.md").read_text() == "Improved skill"

    asyncio.run(scenario())


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


def test_public_refs_and_event_reconnect(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    other = make_store(tmp_path, "other")
    ref = other.put("reports", "a", {"text": "other run"})
    with pytest.raises(ValueError, match="outside"):
        store.public_record(ref)
    for uri in (
        "run://example/private/labels.json",
        "run://example/agents/rsi/session.json",
        "run://example/reports/%2e%2e/private/labels.json",
    ):
        with pytest.raises(ValueError):
            store.public_record(RecordRef(uri))
    store.event("run_started")
    store.event("report_ready", agent_id="ra", iteration_id="node1", report_id="report1")
    reopened = ResearchStore(tmp_path, "example")
    reopened.event("decision", reason="More evidence needed")
    assert [event.sequence for event in reopened.events(after=1)] == [2, 3]


def test_invalid_constraints_and_report_are_rejected() -> None:
    with pytest.raises(ValueError):
        ResearchLimits(max_agents=0)
    with pytest.raises(ValueError):
        ResearchLimits(max_wall_seconds=float("nan"))
    with pytest.raises(ValueError, match="nonempty message"):
        ResearchReport("r", "n", "a", "", now())
