from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from test_passk import ScriptedRuntime, setup

from areal_rsi.api.base import RecordRef
from areal_rsi.coordination.collaboration import build_plan, presets, selected_modules
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.coordination.engine import ToolSet
from areal_rsi.modules.agent_runtime import ToolCall


@pytest.mark.parametrize("extra", [(), ("researcher_profiles", "objective_sharing")])
def test_problem_recipient_persists_and_solution_can_reach_the_team(
    tmp_path: Path, extra: tuple[str, ...]
) -> None:
    async def scenario() -> None:
        c, _, agents = await setup(tmp_path, 4)
        source, helper, third, fourth = agents
        plan = build_plan("passk", ("leader_guidance", "problem_sharing", *extra))
        actions = plan.actions(c)
        problem = c.store.put("materials", "problem", {"agent_id": source, "text": "Failure"})
        args = {
            "agent_id": helper,
            "message": "Can this be fixed?",
            "references": [problem.uri],
            "experience": {"id": "range", "kind": "problem", "source_agent_id": source},
        }
        send = actions.handler("rsi")
        first = await send(ToolCall("advise_researcher", args, "first"))
        assert first.output["status"] == "queued"
        assert (await send(ToolCall("advise_researcher", args, "first"))).output == first.output
        # A retry or another turn cannot create a fresh recipient allowance.
        restored = Coordinator(c.store)
        actions = plan.actions(restored)
        send = actions.handler("rsi")
        before = list(restored.state["inboxes"].get(third, []))
        denied = await send(ToolCall("advise_researcher", {**args, "agent_id": third}, "other"))
        assert "不能再增加接收者" in denied.output["error"]
        assert restored.state["inboxes"].get(third, []) == before
        assert (await send(ToolCall("advise_researcher", args, "followup"))).output[
            "status"
        ] == "queued"
        back = await send(ToolCall("advise_researcher", {**args, "agent_id": source}, "origin"))
        assert back.output["status"] == "queued"
        unmarked = await send(
            ToolCall("advise_researcher", {**args, "experience": None}, "unmarked")
        )
        assert "experience" in unmarked.output["error"]
        positive = {**args, "experience": {**args["experience"], "kind": "positive"}}
        denied = await send(ToolCall("advise_researcher", positive, "relabel"))
        assert "新的解决或验证记录" in denied.output["error"]
        proof = c.store.put("materials", "solution", {"agent_id": helper, "text": "Verified fix"})
        positive.update(
            references=[proof.uri],
            experience={"id": "range", "kind": "positive", "source_agent_id": helper},
        )
        for target in (source, third, fourth):
            result = await send(
                ToolCall("advise_researcher", {**positive, "agent_id": target}, target)
            )
            assert result.output["status"] == "queued"
        entry = restored.state["problem_sharing"]["range"]
        assert entry["origin_agent_id"] == source and entry["problem_recipient"] == helper
        assert set(entry["recipients"]) == set(agents)
        # Status/recovery exposes the ledger to RSI, without broadcasting reports.
        restored.state["passk_reports"] = {source: {"message": "Unresolved detail"}}
        assert "latest_report" not in next(
            r for r in actions.state(third)["researchers"] if r["agent_id"] == source
        )
        assert actions.read_record(third, proof.uri)["text"] == "Verified fix"
        engine = plan.engine(
            restored, ScriptedRuntime(), lambda aid: ToolSet([], actions.handler(aid))
        )
        assert engine._recovery_state("rsi")["shared_experiences"]["range"] == entry
        restored.stop_agent(fourth, "finished")
        stopped = await send(
            ToolCall("advise_researcher", {**positive, "agent_id": fourth}, "stopped")
        )
        assert "已经停止" in stopped.output["error"]

    asyncio.run(scenario())


def test_v0211_is_a_separate_v021_recipe() -> None:
    choice = next(p for p in presets() if p["id"] == "passk_guided_v0211")
    assert choice["modules"] == ["leader_guidance", "problem_sharing"]
    old = build_plan("passk", ("leader_guidance",))
    new = build_plan("passk", tuple(choice["modules"]))
    assert issubclass(new.actions, old.actions) and issubclass(new.engine, old.engine)
    assert old.rsi_prompt != new.rsi_prompt and old.research_prompt != new.research_prompt
    for version in ("leader_guidance_v022", "leader_guidance_v023", "leader_guidance_v024"):
        with pytest.raises(ValueError, match="冲突"):
            selected_modules("passk", (*choice["modules"], version))


def test_guidance_accepts_candidate_verification_and_job_evidence(tmp_path: Path) -> None:
    async def scenario():
        c, _, agents = await setup(tmp_path, 4)
        actions = build_plan("passk", ("leader_guidance", "problem_sharing")).actions(c)
        source, recipient = agents[:2]
        workspace = tmp_path / "candidate"
        workspace.mkdir()
        (workspace / "source.txt").write_text("evidence")
        candidate = await c.store.snapshot(workspace, agent_id=source, description="candidate")
        references = [
            c.store.record_reference(RecordRef("candidates/" + candidate.candidate_id)),
            c.store.put("verifications", "verified", {"candidate_id": candidate.candidate_id}),
            c.store.put(
                "executions", "job-evidence", {"job_id": "job-evidence", "agent_id": source}
            ),
        ]
        for i, ref in enumerate(references):
            result = await actions.handler("rsi")(
                ToolCall(
                    "advise_researcher",
                    {
                        "agent_id": recipient,
                        "message": "Check the original evidence",
                        "references": [ref.uri],
                        "experience": {
                            "id": f"evidence-{i}",
                            "kind": "positive",
                            "source_agent_id": source,
                        },
                    },
                    f"advice-{i}",
                )
            )
            assert result.output["status"] == "queued"

    asyncio.run(scenario())
