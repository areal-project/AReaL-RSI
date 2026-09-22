"""The workbench describes complete independent attempts, not their last report/job."""

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

import pytest

from areal_rsi.api.research import ResearchLimits, TaskBrief, VerificationContract, VerifiedResult
from areal_rsi.base.storage import encode, write_json
from areal_rsi.coordination.config import ResearchConfig
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.coordination.passk import PassKActions
from areal_rsi.execution import JobRecord, JobSpec
from areal_rsi.research_app import ResearchApp
from areal_rsi.ui.workbench import public_detail


def fixture(root: Path, direction: Literal["maximize", "minimize"] = "maximize") -> Coordinator:
    config = ResearchConfig(
        TaskBrief(
            "Independent research",
            "Keep exploring",
            limits=ResearchLimits(max_agents=2),
            verification=VerificationContract(
                "speedup",
                direction=direction,
            ),
        ),
        model_settings_pinned=True,
    )
    c = Coordinator.create(root, config.brief)
    write_json(c.store.root / "coordination/config.json", config.public())
    c.state["mode"] = "passk"
    c.start()
    c.spawn(key="attempt", direction="Independent attempt", instruction="Solve the whole task")
    return c


@pytest.mark.parametrize("direction", ["maximize", "minimize"])
def test_node_score_is_valid_search_best_regardless_of_job_order(
    tmp_path: Path, direction: Literal["maximize", "minimize"]
) -> None:
    async def scenario() -> None:
        c = fixture(tmp_path, direction)
        actor = c.store.run().research_agents[0]
        workspace = tmp_path / "candidate"
        workspace.mkdir()
        jobs: dict[str, Any] = {}
        candidates = []
        # IDs, timestamps and scores deliberately disagree. A later failed,
        # withdrawn, foreign-contract, simulated or final result cannot win.
        for index, (name, value) in enumerate(
            [
                ("job-a-best", 3.0),
                ("job-z-latest", 1.0),
                ("job-b-withdrawn", 30.0),
                ("job-c-wrong", 40.0),
                ("job-d-final", 50.0),
                ("job-e-contract", 60.0),
                ("job-f-simulated", 70.0),
                ("job-g-failed", 80.0),
            ]
        ):
            (workspace / "model.py").write_text(name)
            candidate = await c.store.snapshot(workspace, agent_id=actor.agent_id, description=name)
            candidates.append(candidate)
            result = VerifiedResult(
                name,
                candidate.candidate_id,
                c.store.contract_digest(),
                f"2026-09-16T10:{index:02d}:00+00:00",
                "succeeded",
                score=value if direction == "maximize" else -value,
                passed=True,
            )
            if name == "job-c-wrong":
                result.passed = False
            elif name == "job-d-final":
                result.phase = "final"
            elif name == "job-e-contract":
                result.contract_digest = "other-protocol"
            elif name == "job-f-simulated":
                result.simulated = True
            elif name == "job-g-failed":
                result = replace(result, status="infra_error", score=None, passed=None)
            jobs[name] = {
                "agent_id": actor.agent_id,
                "iteration_id": actor.current_iteration_id,
                "candidate_id": candidate.candidate_id,
                "verification": True,
                "phase": result.phase,
                "result": encode(result),
                "output": str(tmp_path / name),
            }
            write_json(
                c.store.root / "jobs" / name / "job.json",
                JobRecord(name, JobSpec(), "succeeded", float(index), float(index)),
            )
        c.store.withdraw_candidate(candidates[2].candidate_id, "Counterexample", ())
        write_json(c.store.root / "coordination/jobs.json", jobs)
        detail = public_detail(await ResearchApp(tmp_path).detail(c.store.run_id))
        assert detail["nodes"][0]["verification"]["result_id"] == "job-a-best"
        # Withdrawal changes the displayed best while retaining all job history.
        c.store.withdraw_candidate(candidates[0].candidate_id, "Another counterexample", ())
        detail = public_detail(await ResearchApp(tmp_path).detail(c.store.run_id))
        assert detail["nodes"][0]["verification"]["result_id"] == "job-z-latest"
        c.store.withdraw_candidate(candidates[1].candidate_id, "No valid candidate remains", ())
        detail = public_detail(await ResearchApp(tmp_path).detail(c.store.run_id))
        assert detail["nodes"][0]["verification"] is None
        assert len(detail["jobs"]) == 8

    asyncio.run(scenario())


@pytest.mark.parametrize("legacy_events", [False, True])
def test_reports_have_history_delivery_status_and_paginated_events_without_mutation(
    tmp_path: Path, legacy_events: bool
) -> None:
    async def scenario() -> None:
        c = fixture(tmp_path)
        actor = c.store.run().research_agents[0]
        actions = PassKActions(c)
        c.begin_turn(actor.agent_id)
        for key in ("first", "second"):
            actions.progress(actor.agent_id, key, f"progress {key}", [])
        report_events = [e for e in c.store.events() if e.kind == "report_submitted"]
        assert len(report_events) == 2
        c.state["delivered"].append(report_events[0].data["message_id"])
        c.save()
        if legacy_events:
            for event in report_events:
                write_json(
                    c.store.root / "events" / f"{event.sequence:012d}.json",
                    replace(
                        event,
                        kind="message",
                        agent_id=None,
                        iteration_id=None,
                        data={k: v for k, v in event.data.items() if k != "report_id"},
                    ),
                )
        c.stop("Deadline")
        before = {p: p.read_bytes() for p in c.store.root.rglob("*.json")}
        app = ResearchApp(tmp_path)
        detail = public_detail(await app.detail(c.store.run_id))
        node = detail["nodes"][0]
        assert node["state"] == "cancelled"
        assert [r["message"] for r in node["reports"]] == ["progress first", "progress second"]
        assert [r["delivery_status"] for r in node["reports"]] == ["delivered", "pending"]
        assert node["report"]["report_id"] == node["reports"][-1]["report_id"]
        assert node["updated_at"] >= node["reports"][-1]["created_at"]
        last_sequence = c.store.events()[-1].sequence
        for event in report_events:
            page = public_detail(
                await app.detail(
                    c.store.run_id,
                    after=event.sequence - 1,
                    event_limit=last_sequence - event.sequence + 1,
                )
            )
            assert len(page["events"]) == last_sequence - event.sequence + 1
            visible = page["events"][0]
            assert visible["kind"] == "report_submitted"
            assert visible["created_at"] == event.created_at
            assert visible["agent_id"] == actor.agent_id
            assert visible["data"]["report_id"] == event.data["report_id"]
            assert page["event_cursor"] == last_sequence
        assert {p: p.read_bytes() for p in before} == before

    asyncio.run(scenario())


def test_graph_attributes_explicit_sources_without_mutating_or_reading_other_runs(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        from areal_rsi.api.base import RecordRef

        c = fixture(tmp_path)
        source = c.store.run().research_agents[0]
        c.spawn(key="second", direction="Another attempt", instruction="Explore independently")
        target = c.store.run().research_agents[1]
        c.begin_turn(source.agent_id)
        PassKActions(c).progress(source.agent_id, "breakthrough", "A concrete finding", [])
        report = next(iter(c.store.root.joinpath("reports").glob("*.json")))
        ref = c.store.paths.reference(report)
        workspace = tmp_path / "candidate"
        workspace.mkdir()
        (workspace / "result.txt").write_text("Original implementation")
        candidate = await c.store.snapshot(
            workspace, agent_id=source.agent_id, description="Finding"
        )
        candidate_ref = c.store.paths.reference(
            c.store.root / "candidates" / f"{candidate.candidate_id}.json"
        )
        excerpt = c.store.put("materials", "excerpt", {"agent_id": "rsi", "job_id": "experiment"})
        write_json(
            c.store.root / "coordination/jobs.json",
            {"experiment": {"agent_id": source.agent_id, "output": str(tmp_path / "output")}},
        )
        write_json(
            c.store.root / "jobs/experiment/job.json",
            JobRecord("experiment", JobSpec(), "succeeded", 1, 2),
        )
        shared = c.enqueue(
            "rsi",
            target.agent_id,
            "Try this independently",
            key="share",
            references=(ref, candidate_ref, excerpt),
        )
        # Identical report IDs from another run must not acquire local attribution.
        foreign = RecordRef(ref.uri.replace(f"run://{c.store.run_id}/", "run://another-run/"))
        unknown = c.enqueue(
            "rsi",
            target.agent_id,
            "Mentioning another researcher is not provenance",
            key="unknown",
            references=(foreign,),
        )
        c.state["delivered"].append(shared)
        c.save()
        before = {p: p.read_bytes() for p in c.store.root.rglob("*.json")}
        detail = public_detail(
            await ResearchApp(tmp_path).detail(c.store.run_id, after=999999, event_limit=1)
        )
        messages = {m["message_id"]: m for m in detail["communications"]}
        assert len(messages) == 3  # Report and two suggestions, initial assignments excluded.
        assert {s["agent_id"] for s in messages[shared]["sources"]} == {source.agent_id}
        assert {s["uri"] for s in messages[shared]["sources"]} == {
            ref.uri, candidate_ref.uri, excerpt.uri
        }
        assert messages[shared]["delivery_status"] == "delivered"
        assert messages[unknown]["sources"] == []
        assert messages[unknown]["delivery_status"] == "pending"
        assert detail["events"] == []  # Provenance survives event pagination/reloading.
        assert {p: p.read_bytes() for p in before} == before

    asyncio.run(scenario())
