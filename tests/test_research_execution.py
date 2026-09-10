from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from areal_rsi.api.research import (
    CandidateSnapshot,
    TaskBrief,
    VerificationContract,
    VerifiedResult,
)
from areal_rsi.base.storage import now
from areal_rsi.coordination.actions import ResearchActions
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.coordination.jobs import ResearchExecution
from areal_rsi.execution import ApprovedCommand, JobRecord
from areal_rsi.modules.responses import ToolCall


@pytest.mark.parametrize("unlimited", [False, True])
def test_background_verification_wakes_only_waiting_assignment(
    tmp_path: Path, unlimited: bool
) -> None:
    async def scenario() -> None:
        c = Coordinator.create(
            tmp_path,
            TaskBrief(
                "Improve", "Use the trusted verifier", verification=VerificationContract("accuracy")
            ),
            simulated=True,
        )
        c.start()
        if unlimited:
            run = c.store.run()
            brief = c.store.brief()
            brief.limits = replace(brief.limits, max_verify_calls=None, max_jobs=None)
            run.brief_ref = c.store.put("briefs", "unlimited", brief)
            c.store.save_run(run)
        assignment = c.spawn(
            key="a",
            direction="A",
            instruction="Build and check",
            completion_criteria="Report the measured result",
        )
        agent_id = assignment["agent_id"]
        c.begin_turn(agent_id)
        actions = ResearchActions(c)
        candidate = await c.store.snapshot(
            actions.workspace(agent_id), agent_id=agent_id, description="A first candidate"
        )

        def verify(record: JobRecord, snapshot: CandidateSnapshot, phase: str) -> VerifiedResult:
            return VerifiedResult(
                record.job_id,
                snapshot.candidate_id,
                c.store.contract_digest(),
                now(),
                "succeeded",
                0.7,
                True,
                phase=phase,  # type: ignore[arg-type]
                simulated=True,
            )

        execution = ResearchExecution(
            actions,
            simulated=True,
            mock_duration=0.03,
            verifier=ApprovedCommand(
                "verify",
                ("configured-verifier", "{workspace}", "{output}"),
                ("workspace", "output"),
            ),
            read_verification=verify,
        )
        try:
            handler = actions.handler(agent_id)
            call = ToolCall(
                "verify_candidate",
                {"candidate_id": candidate.candidate_id, "phase": "search"},
                "verify-1",
            )
            job = await handler(call)
            repeated = await handler(call)
            assert repeated == job and len(execution.manager.records()) == 1
            waiting = await handler(
                ToolCall("wait_for_job", {"job_id": job.output["job_id"]}, "wait")
            )
            assert waiting.stop
            c.end_turn(agent_id)
            assert agent_id not in c.pending()
            for _ in range(100):
                if not await execution.poll():
                    break
                await asyncio.sleep(0.01)
            assert agent_id in c.pending()
            assert c.actor(agent_id).current_iteration_id == assignment["iteration_id"]
            assert len(c.actor(agent_id).iterations) == 1
            assert c.store.run().baseline_result_ref is None
            assert c.store.run().best_result_ref is not None
            assert c.state["verify_calls"] == 1
            status = await handler(
                ToolCall("job_status", {"job_id": job.output["job_id"]}, "status")
            )
            assert status.output["result"]["simulated"]
            assert status.output["result"]["uri"].startswith("run://")
            private = await handler(
                ToolCall("read_job_log", {"job_id": job.output["job_id"]}, "log")
            )
            assert "private" in private.output["error"].lower()
        finally:
            execution.close(cancel=True)

    asyncio.run(scenario())


def test_process_stdout_is_not_a_score_and_outputs_can_be_collected(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Coordinator.create(tmp_path, TaskBrief("Make a file", "Inspect execution output"))
        c.start()
        assignment = c.spawn(
            key="a",
            direction="A",
            instruction="Create a file",
            completion_criteria="Read the job's output",
        )
        agent_id = assignment["agent_id"]
        actions = ResearchActions(c)
        command = ApprovedCommand(
            "example",
            (
                sys.executable,
                "-c",
                "import pathlib,sys; "
                "out=pathlib.Path(sys.argv[1]); (out/'trained').mkdir(); "
                "(out/'trained'/'weights.txt').write_text('artifact'); "
                "(out/'private.txt').write_text('private-label'); "
                "print('{\"score\": 1.0}')",
                "{output}",
            ),
            ("output",),
            output_subdir="trained",
        )
        execution = ResearchExecution(actions, commands={"example": command})
        try:
            handler = actions.handler(agent_id)
            job = await handler(
                ToolCall(
                    "run_job",
                    {"command_name": "example", "candidate_id": "", "parameters_json": "{}"},
                    "job1",
                )
            )
            for _ in range(300):
                if not await execution.poll():
                    break
                await asyncio.sleep(0.01)
            status = await handler(
                ToolCall("job_status", {"job_id": job.output["job_id"]}, "status")
            )
            assert status.output["state"] == "succeeded"
            assert "score" not in status.output["result"]
            assert c.store.run().best_result_ref is None
            read = await handler(
                ToolCall(
                    "read_job_output_file",
                    {
                        "job_id": job.output["job_id"],
                        "path": "weights.txt",
                        "offset": 0,
                    },
                    "read",
                )
            )
            assert read.output["content"] == "artifact"
            assert read.output["uri"].startswith("run://")
            assert not (actions.workspace(agent_id) / "weights.txt").exists()
            private = await handler(
                ToolCall(
                    "read_job_output_file",
                    {
                        "job_id": job.output["job_id"],
                        "path": "../private.txt",
                        "offset": 0,
                    },
                    "private",
                )
            )
            assert "error" in private.output
            collected = await handler(
                ToolCall(
                    "collect_job_output",
                    {
                        "job_id": job.output["job_id"],
                        "destination": "trained",
                    },
                    "collect",
                )
            )
            assert "error" not in collected.output
            assert (
                actions.workspace(agent_id) / "trained" / "weights.txt"
            ).read_text() == "artifact"
            assert not (actions.workspace(agent_id) / "trained" / "private.txt").exists()
        finally:
            execution.close(cancel=True)

    asyncio.run(scenario())


def test_job_subscription_survives_other_messages_and_notifies_rsi_observer(tmp_path: Path) -> None:
    async def scenario() -> None:
        from areal_rsi.api.research import ResearchMessage

        c = Coordinator.create(
            tmp_path, TaskBrief("Research", "Wait for actual work"), simulated=True
        )
        c.start()
        c.begin_turn("rsi")
        c.wait("rsi", "Waiting for reports")
        c.end_turn("rsi")
        assignment = c.spawn(
            key="a", direction="A", instruction="Run", completion_criteria="Report"
        )
        researcher = assignment["agent_id"]
        c.begin_turn(researcher)
        actions = ResearchActions(c)
        execution = ResearchExecution(
            actions,
            simulated=True,
            mock_duration=0.05,
            commands={
                "task": ApprovedCommand("task", ("configured-command",), ()),
            },
        )
        try:
            call = ToolCall(
                "run_job",
                {
                    "command_name": "task",
                    "candidate_id": "",
                    "parameters_json": "{}",
                },
                "task",
            )
            job = await actions.handler(researcher)(call)
            job_id = job.output["job_id"]
            for agent_id in [researcher, "rsi"]:
                await actions.handler(agent_id)(
                    ToolCall("wait_for_job", {"job_id": job_id}, "wait")
                )
            c.end_turn(researcher)
            c.enqueue("user", "rsi", "状态如何？", key="status-question")
            c.begin_turn("rsi")
            c.wait("rsi", "已经回复用户，继续等执行结果")
            c.end_turn("rsi")
            assert c.pending() == []
            for _ in range(100):
                if not await execution.poll():
                    break
                await asyncio.sleep(0.01)
            assert set(c.pending()) == {researcher, "rsi"}
            notices = [
                m for m in c.store.records("messages", ResearchMessage) if m.sender == "system"
            ]
            assert len(notices) == 2
            for agent_id in [researcher, "rsi"]:
                c.begin_turn(agent_id)
                c.end_turn(agent_id)
            for _ in range(3):
                await execution.poll()
            assert c.pending() == []
            assert (
                len(
                    [
                        m
                        for m in c.store.records("messages", ResearchMessage)
                        if m.sender == "system"
                    ]
                )
                == 2
            )
        finally:
            execution.close(cancel=True)

    asyncio.run(scenario())


def test_completed_legacy_job_recovers_missing_delivery_after_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        from areal_rsi.coordination.store import ResearchStore

        c = Coordinator.create(
            tmp_path, TaskBrief("Research", "Continue after baseline"), simulated=True
        )
        c.start()
        c.begin_turn("rsi")
        actions = ResearchActions(c)
        commands = {"task": ApprovedCommand("task", ("configured-command",), ())}
        execution = ResearchExecution(
            actions, simulated=True, mock_duration=0.02, commands=commands
        )
        job = await actions.handler("rsi")(
            ToolCall(
                "run_job",
                {
                    "command_name": "task",
                    "candidate_id": "",
                    "parameters_json": "{}",
                },
                "task",
            )
        )
        job_id = job.output["job_id"]
        c.wait("rsi", "等待起点评测结束")
        c.end_turn("rsi")
        for _ in range(100):
            if not await execution.poll():
                break
            await asyncio.sleep(0.01)
        # Reproduce the old state: result processed, subscribed, but no completion message.
        info = execution.jobs[job_id]
        assert info["processed"]
        info["waiting"] = True
        info.pop("waiters", None)
        execution.save()
        execution.close()
        recovered = Coordinator(ResearchStore(tmp_path, c.store.run_id))
        resumed = ResearchExecution(ResearchActions(recovered), simulated=True, commands=commands)
        try:
            await resumed.poll()
            assert recovered.pending() == ["rsi"]
            before = list(recovered.state["inboxes"]["rsi"])
            # Also repair a lost notified marker without duplicating an existing message.
            resumed.jobs[job_id].pop("waiters")
            await resumed.poll()
            assert recovered.state["inboxes"]["rsi"] == before
            assert resumed.jobs[job_id]["waiters"]["rsi"]["notified"]
        finally:
            resumed.close()

    asyncio.run(scenario())


def test_late_completion_does_not_cross_report_or_stop_barrier(tmp_path: Path) -> None:
    async def scenario() -> None:
        from areal_rsi.api.research import ResearchMessage

        for finish in ("report", "stop"):
            c = Coordinator.create(
                tmp_path / finish, TaskBrief("Research", "Respect report boundary"), simulated=True
            )
            c.start()
            assignment = c.spawn(
                key="a", direction="A", instruction="Run", completion_criteria="Report"
            )
            agent_id = assignment["agent_id"]
            c.begin_turn(agent_id)
            actions = ResearchActions(c)
            execution = ResearchExecution(
                actions,
                simulated=True,
                mock_duration=0.02,
                commands={
                    "task": ApprovedCommand("task", ("configured-command",), ()),
                },
            )
            try:
                job = await actions.handler(agent_id)(
                    ToolCall(
                        "run_job",
                        {
                            "command_name": "task",
                            "candidate_id": "",
                            "parameters_json": "{}",
                        },
                        "task",
                    )
                )
                await actions.handler(agent_id)(
                    ToolCall("wait_for_job", {"job_id": job.output["job_id"]}, "wait")
                )
                if finish == "report":
                    c.report(
                        agent_id,
                        key="report",
                        fields={
                            field: "已归档当前执行情况，等待 RSI 指令"
                            for field in [
                                "task_status",
                                "work",
                                "problems",
                                "results",
                                "interpretation",
                                "next_steps",
                            ]
                        },
                    )
                else:
                    c.stop_agent(agent_id, "RSI stopped this direction")
                c.end_turn(agent_id)
                for _ in range(100):
                    if not await execution.poll():
                        break
                    await asyncio.sleep(0.01)
                assert agent_id not in c.pending()
                assert not [
                    m
                    for m in c.store.records("messages", ResearchMessage)
                    if m.sender == "system" and m.recipient == agent_id
                ]
            finally:
                execution.close(cancel=True)

    asyncio.run(scenario())
