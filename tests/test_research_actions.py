from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Literal, cast
from unittest.mock import patch

import pytest

from areal_rsi.api.base import AgentState
from areal_rsi.api.research import (
    CandidateSnapshot,
    ResearchLimits,
    TaskBrief,
    VerificationContract,
    VerifiedResult,
)
from areal_rsi.base.storage import now
from areal_rsi.coordination.actions import ResearchActions
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.modules.responses import ToolCall


def test_role_permissions_and_report_stop(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Coordinator.create(tmp_path, TaskBrief("Study", "No baseline", ResearchLimits()))
        c.start()
        actions = ResearchActions(c)
        assignment = await actions.handler("rsi")(
            ToolCall(
                "spawn_researcher",
                {
                    "direction": "Environment",
                    "instruction": "Inspect the configured environment",
                    "completion_criteria": "Report exact supported interface",
                    "references": [],
                },
                "spawn-1",
            )
        )
        researcher = assignment.output["agent_id"]
        denied = await actions.handler(researcher)(ToolCall("spawn_researcher", {}, "forbidden"))
        assert "error" in denied.output
        report: dict[str, Any] = {
            "task_status": "Completed",
            "work": "Compared configured command and docs",
            "problems": "GPU unavailable",
            "results": "Command accepts the expected arguments",
            "interpretation": "Interface matches; execution is untested",
            "next_steps": "Provision GPU and execute one smoke job",
            "shared_findings": [],
            "references": [],
        }
        result = await actions.handler(researcher)(ToolCall("submit_report", report, "report-1"))
        assert result.stop and result.reason == "rsi_reply"
        assert c.actor(researcher).state == AgentState.WAITING
        repeated = await actions.handler(researcher)(ToolCall("submit_report", report, "report-1"))
        assert repeated == result
        assert c.actor(researcher).iterations[-1].report_ref is not None

    asyncio.run(scenario())


def test_workspace_escape_and_scope_are_enforced(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Coordinator.create(
            tmp_path, TaskBrief("Study", "Only skills", ResearchLimits(allowed_paths=("skills/*",)))
        )
        c.start()
        assignment = c.spawn(
            key="a",
            direction="A",
            instruction="Write a skill",
            completion_criteria="Save the candidate",
        )
        agent_id = assignment["agent_id"]
        actions = ResearchActions(c)
        handler = actions.handler(agent_id)
        outside = tmp_path / "private.txt"
        outside.write_text("private")
        (actions.workspace(agent_id) / "link").symlink_to(outside)
        denied = await handler(ToolCall("read_file", {"path": "link"}, "escape"))
        assert "error" in denied.output
        (actions.workspace(agent_id) / "link").unlink()
        await handler(ToolCall("write_file", {"path": "prompt.md", "content": "Changed"}, "write"))
        result = await handler(ToolCall("save_candidate", {"description": "Illegal scope"}, "save"))
        assert "outside" in result.output["error"]
        assert not (c.store.root / "candidates").exists()

    asyncio.run(scenario())


def test_interaction_keeps_natural_brief_before_start(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Coordinator.create(tmp_path, TaskBrief("Unclear goal", "Initial words"))
        c.enqueue("user", "interaction", "Please clarify my goal", key="user-1")
        assert c.pending() == ["interaction"]
        actions = ResearchActions(c)
        updated = await actions.handler("interaction")(
            ToolCall(
                "update_brief",
                {
                    "brief_json": '{"objective":"Optimize a reusable kernel skill",'
                    '"instructions":"Prefer simple approaches; fusion is only a suggestion",'
                    '"limits":{"allowed_paths":["skills/*"]}}',
                },
                "brief-1",
            )
        )
        assert "error" not in updated.output
        assert c.store.brief().instructions.endswith("only a suggestion")
        assert c.store.brief().limits.allowed_paths == ("skills/*",)
        c.start()
        refused = await actions.handler("interaction")(
            ToolCall(
                "update_brief",
                {
                    "brief_json": '{"objective":"Different goal","instructions":"Change limits"}',
                },
                "brief-2",
            )
        )
        assert "frozen" in refused.output["error"]

    asyncio.run(scenario())


def test_candidate_history_handoff_and_unsaved_work_are_preserved(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Coordinator.create(
            tmp_path,
            TaskBrief(
                "Improve skills",
                "Keep prompt fixed",
                ResearchLimits(max_agents=2, allowed_paths=("skills/*",)),
            ),
        )
        seed_files = tmp_path / "seed"
        (seed_files / "skills").mkdir(parents=True)
        (seed_files / "prompt.md").write_text("fixed")
        (seed_files / "skills/guide.md").write_text("seed")
        seed = await c.store.snapshot(seed_files, agent_id="operator", description="seed")
        c.state["seed_candidate_id"] = seed.candidate_id
        c.save()
        c.start()
        actions = ResearchActions(c)
        agents = []
        for name in ("a", "b"):
            assignment = c.spawn(
                key=name, direction=name, instruction="Improve", completion_criteria="Report"
            )
            agents.append(assignment["agent_id"])
            await actions.initialize_workspace(agents[-1])
        first, second = agents
        one = actions.handler(first)
        for index in (1, 2):
            await one(
                ToolCall(
                    "write_file",
                    {"path": "skills/guide.md", "content": str(index)},
                    f"write{index}",
                )
            )
            saved = await one(
                ToolCall("save_candidate", {"description": str(index)}, f"save{index}")
            )
            if index == 1:
                first_id = saved.output["candidate_id"]
                assert saved.output["parent_candidate_id"] == seed.candidate_id
            else:
                assert saved.output["parent_candidate_id"] == first_id
        adopted_id = saved.output["candidate_id"]
        two = actions.handler(second)
        await two(
            ToolCall("write_file", {"path": "skills/guide.md", "content": "unsaved"}, "write")
        )
        refused = await two(ToolCall("checkout_candidate", {"candidate_id": adopted_id}, "dirty"))
        assert "unsaved" in refused.output["error"]
        assert (actions.workspace(second) / "skills/guide.md").read_text() == "unsaved"
        own = await two(ToolCall("save_candidate", {"description": "Preserve work"}, "save"))
        checked = await two(
            ToolCall("checkout_candidate", {"candidate_id": adopted_id}, "checkout")
        )
        assert checked.output["checked_out"]
        assert actions.workspace_candidate(second) == adopted_id
        assert (actions.workspace(second) / "skills/guide.md").read_text() == "2"
        assert c.store.get("candidates", own.output["candidate_id"], CandidateSnapshot)
        await two(
            ToolCall("write_file", {"path": "skills/guide.md", "content": "combined"}, "merge")
        )
        merged = await two(
            ToolCall("save_candidate", {"description": "Reuse the other direction"}, "merged")
        )
        assert merged.output["parent_candidate_id"] == adopted_id
        # Even an operator-created out-of-scope candidate cannot change this
        # run's authorization boundary when selected as a new parent.
        (seed_files / "prompt.md").write_text("not allowed")
        invalid = await c.store.snapshot(
            seed_files, agent_id="operator", description="Other product"
        )
        refused = await two(
            ToolCall("checkout_candidate", {"candidate_id": invalid.candidate_id}, "scope")
        )
        assert "allowed_paths" in refused.output["error"]
        assert actions.workspace_candidate(second) == merged.output["candidate_id"]

    asyncio.run(scenario())


def test_final_confirmation_belongs_to_selected_candidate_and_reaches_target(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        c = Coordinator.create(
            tmp_path,
            TaskBrief(
                "Improve",
                "Confirm on final split",
                verification=VerificationContract(
                    "accuracy", target=0.7, identity={"require_final_confirmation": True}
                ),
            ),
        )
        actions = ResearchActions(c)
        directory = actions.workspace("rsi")
        (directory / "model.py").write_text("a")
        a = await c.store.snapshot(directory, agent_id="operator", description="a")
        (directory / "model.py").write_text("b")
        b = await c.store.snapshot(directory, agent_id="operator", description="b")
        c.start()

        def record(
            key: str, candidate: CandidateSnapshot, phase: Literal["search", "final"], score: float
        ) -> None:
            result = VerifiedResult(
                key,
                candidate.candidate_id,
                c.store.contract_digest(),
                now(),
                "succeeded",
                score,
                True,
                phase=phase,
            )
            ref = c.store.record_verification(result)
            if phase == "search":
                c.store.consider_best(key)
            else:
                run = c.store.run()
                run.final_result_ref = ref
                c.store.save_run(run)

        record("search-a", a, "search", 0.95)
        finish = actions.handler("rsi")
        args = {"summary": "done", "candidate_id": a.candidate_id}
        no_final = await finish(ToolCall("finish", args, "first"))
        assert "final" in no_final.output["error"]
        record("final-b", b, "final", 0.9)
        wrong_candidate = await finish(ToolCall("finish", args, "second"))
        assert "final" in wrong_candidate.output["error"]
        record("final-a-low", a, "final", 0.6)
        missed_target = await finish(ToolCall("finish", args, "third"))
        assert "target" in missed_target.output["error"]
        assert c.state["status"] == "running"
        record("final-a-good", a, "final", 0.75)
        finished = await finish(ToolCall("finish", args, "fourth"))
        assert finished.stop and c.state["status"] == "succeeded"
        assert c.state["selected_candidate_id"] == a.candidate_id
        assert c.store.public_record(c.store.run().final_result_ref)["result_id"] == "final-a-good"  # type: ignore[arg-type]

    asyncio.run(scenario())


def test_new_failed_final_cannot_reuse_an_old_success(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Coordinator.create(
            tmp_path,
            TaskBrief(
                "Improve",
                "Confirm",
                verification=VerificationContract("loss", direction="minimize", target=0.2),
            ),
        )
        c.state["require_final_confirmation"] = True
        c.save()
        actions = ResearchActions(c)
        candidate = await c.store.snapshot(
            actions.workspace("rsi"), agent_id="operator", description="candidate"
        )
        c.start()
        for key, score, status, stamp in (
            ("good", 0.1, "succeeded", "1"),
            ("new-failure", None, "infra_error", "2"),
        ):
            result = VerifiedResult(
                key,
                candidate.candidate_id,
                c.store.contract_digest(),
                stamp,
                cast(Literal["succeeded", "infra_error"], status),
                score,
                True if score is not None else None,
                phase="final",
                error="GPU failed" if score is None else None,
            )
            c.store.record_verification(result)
        refused = await actions.handler("rsi")(
            ToolCall(
                "finish", {"summary": "Done", "candidate_id": candidate.candidate_id}, "finish"
            )
        )
        assert "final" in refused.output["error"] and c.state["status"] == "running"

    asyncio.run(scenario())


def test_interaction_can_answer_rsi_without_leaving_a_user_question(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Coordinator.create(tmp_path, TaskBrief("Improve", "User already specified two GPUs"))
        c.start()
        actions = ResearchActions(c)
        question = await actions.handler("rsi")(
            ToolCall("ask_user", {"question": "Which resources are available?"}, "ask")
        )
        assert question.stop and c.state["waiting_for_user"] == []
        assert c.actor("rsi").wait_reason == "interaction"
        await actions.handler("interaction")(
            ToolCall("send_to_rsi", {"message": "The brief already states two GPUs"}, "reply")
        )
        assert c.state["waiting_for_user"] == []
        await actions.handler("interaction")(
            ToolCall("ask_user", {"question": "What is the target score?"}, "ask-user")
        )
        assert len(c.state["waiting_for_user"]) == 1

    asyncio.run(scenario())


def test_evidence_can_be_shared_and_referenced_without_constructing_uris(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Coordinator.create(tmp_path, TaskBrief("Study", "Share supported findings"))
        c.start()
        actions = ResearchActions(c)
        agent = c.spawn(key="a", direction="A", instruction="Study", completion_criteria="Report")[
            "agent_id"
        ]
        saved = await actions.handler(agent)(
            ToolCall(
                "save_evidence",
                {
                    "title": "Finding",
                    "summary": "Observed a supported behavior",
                    "references": [],
                    "limitations": "One observation",
                },
                "save",
            )
        )
        assert saved.output["uri"].startswith("run://") and saved.output["evidence_id"]
        shared = await actions.handler("rsi")(
            ToolCall(
                "share_evidence",
                {
                    "evidence_id": saved.output["evidence_id"],
                    "limitations": "Needs replication",
                },
                "share",
            )
        )
        state = await actions.handler(agent)(ToolCall("get_state", {}, "state"))
        visible = next(item for item in state.output["evidence"] if item["shared"])
        assert visible["uri"] == shared.output["uri"]
        read = await actions.handler(agent)(
            ToolCall("read_record", {"uri": visible["uri"]}, "read")
        )
        assert read.output["summary"] == "Observed a supported behavior"

    asyncio.run(scenario())


def test_checkout_recovers_after_workspace_swap_is_interrupted(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Coordinator.create(tmp_path, TaskBrief("Study", "Preserve saved files"))
        actions = ResearchActions(c)
        workspace = actions.workspace("rsi")
        (workspace / "model.py").write_text("old")
        old = await c.store.snapshot(workspace, agent_id="operator", description="old")
        actions.mark_workspace_candidate("rsi", old.candidate_id)
        other = tmp_path / "other"
        other.mkdir()
        (other / "model.py").write_text("new")
        new = await c.store.snapshot(other, agent_id="operator", description="new")
        call = ToolCall("checkout_candidate", {"candidate_id": new.candidate_id}, "checkout")
        rename = Path.rename

        def interrupted(path: Path, destination: Any) -> Path:
            if path.name.endswith("-checkout"):
                raise OSError("Synthetic interruption between directory renames")
            return rename(path, destination)

        with patch.object(Path, "rename", interrupted), pytest.raises(OSError):
            await actions.handler("rsi")(call)
        recovered = ResearchActions(Coordinator(c.store))
        result = await recovered.handler("rsi")(call)
        assert result.output["checked_out"]
        assert (recovered.workspace("rsi") / "model.py").read_text() == "new"
        assert recovered.workspace_candidate("rsi") == new.candidate_id
        exported = await c.store.export(old.candidate_id, tmp_path / "old-export")
        assert (exported / "model.py").read_text() == "old"

    asyncio.run(scenario())


def test_state_evidence_preview_preserves_full_archived_record(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Coordinator.create(tmp_path, TaskBrief("Study", "Keep shared context focused"))
        actions = ResearchActions(c)
        full_summary = "完整研究结论与来源。" * 200
        saved = await actions.handler("rsi")(
            ToolCall(
                "save_evidence",
                {
                    "title": "Detailed finding",
                    "summary": full_summary,
                    "references": [],
                    "limitations": "Not yet experimentally tested",
                },
                "save",
            )
        )
        shared = await actions.handler("rsi")(
            ToolCall(
                "share_evidence",
                {
                    "evidence_id": saved.output["evidence_id"],
                    "limitations": "Scope unchanged",
                },
                "share",
            )
        )
        assert shared.output["summary"] == full_summary
        state = await actions.handler("rsi")(ToolCall("get_state", {}, "state"))
        preview = next(item for item in state.output["evidence"] if item["shared"])
        assert preview["summary"] == full_summary[:1000]
        assert preview["truncated"] is True and preview["summary_chars"] == len(full_summary)
        assert preview["uri"] == shared.output["uri"]
        full = await actions.handler("rsi")(
            ToolCall("read_record", {"uri": preview["uri"]}, "read")
        )
        assert full.output["summary"] == full_summary
        assert "truncated" not in full.output

    asyncio.run(scenario())


def test_rsi_can_stop_an_unmet_run_without_changing_scores(tmp_path: Path) -> None:
    from areal_rsi.api.base import IterationState, RunState
    from areal_rsi.api.research import ResearchMessage
    from areal_rsi.coordination.jobs import ResearchExecution
    from areal_rsi.execution import ApprovedCommand

    async def scenario() -> None:
        c = Coordinator.create(
            tmp_path,
            TaskBrief(
                "Reach the target",
                "Keep all measured evidence",
                verification=VerificationContract(
                    "accuracy", target=0.9, identity={"require_final_confirmation": True}
                ),
            ),
            simulated=True,
        )
        c.start()
        actions = ResearchActions(c)
        candidate = await c.store.snapshot(
            actions.workspace("rsi"), agent_id="operator", description="Measured candidate"
        )
        for phase in ("search", "final"):
            result = VerifiedResult(
                "result-" + phase,
                candidate.candidate_id,
                c.store.contract_digest(),
                now(),
                "succeeded",
                0.3,
                True,
                phase=phase,
                simulated=True,
            )
            ref = c.store.record_verification(result)
            if phase == "search":
                c.store.consider_best(result.result_id, simulated_run=True)
            else:
                run = c.store.run()
                run.final_result_ref = ref
                c.store.save_run(run)
        before = c.store.run()
        measured = {p.name: p.read_bytes() for p in (c.store.root / "verifications").glob("*.json")}
        finish = await actions.handler("rsi")(
            ToolCall(
                "finish",
                {
                    "candidate_id": candidate.candidate_id,
                    "summary": "Completed",
                },
                "finish",
            )
        )
        assert "target" in finish.output["error"]
        assignment = c.spawn(
            key="a", direction="A", instruction="Explore", completion_criteria="Report findings"
        )
        agent = assignment["agent_id"]
        denied = await actions.handler(agent)(ToolCall("stop_run", {"reason": "Give up"}, "denied"))
        assert "not available" in denied.output["error"] and c.state["status"] == "running"
        execution = ResearchExecution(
            actions,
            simulated=True,
            mock_duration=60,
            commands={"train": ApprovedCommand("train", ("synthetic-training",))},
        )
        try:
            jobs = []
            for key in ("one", "two"):
                output = await actions.handler(agent)(
                    ToolCall(
                        "run_job",
                        {
                            "command_name": "train",
                            "candidate_id": "",
                            "parameters_json": "{}",
                        },
                        key,
                    )
                )
                jobs.append(output.output["job_id"])
            assert {execution.manager.get(key).state for key in jobs} == {"running", "queued"}
            c.state["waiting_for_user"] = [{"message_id": "pending", "question": "More budget?"}]
            c.save()
            reason = (
                "The supported score is simulated 0.30, below 0.90. Further work is not justified "
                "by the remaining evidence and resources."
            )
            call = ToolCall("stop_run", {"reason": reason}, "stop")
            stopped = await actions.handler("rsi")(call)
            assert stopped.stop and stopped.output["status"] == "stopped"
            assert c.state["status"] == "stopped" and c.store.run().state == RunState.CANCELLED
            assert all(actor.state == AgentState.STOPPED for actor in c.actors())
            assert c.actor(agent).iterations[0].state == IterationState.CANCELLED
            assert c.state["waiting_for_user"] == [] and c.pending() == []
            await execution.poll()
            assert all(execution.manager.get(key).state == "cancelled" for key in jobs)
            after = c.store.run()
            assert (after.best_result_ref, after.final_result_ref) == (
                before.best_result_ref,
                before.final_result_ref,
            )
            assert {
                p.name: p.read_bytes() for p in (c.store.root / "verifications").glob("*.json")
            } == measured
            message = c.store.get("messages", stopped.output["message_id"], ResearchMessage)
            assert (message.sender, message.recipient, message.text) == ("rsi", "user", reason)
            count = len(c.store.events())
            repeated = await actions.handler("rsi")(call)
            assert repeated == stopped and len(c.store.events()) == count
        finally:
            execution.close(cancel=True)

    asyncio.run(scenario())
