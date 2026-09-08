from __future__ import annotations

import asyncio
import sys
from pathlib import Path

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


def test_background_verification_wakes_only_waiting_assignment(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Coordinator.create(
            tmp_path,
            TaskBrief(
                "Improve", "Use the trusted verifier", verification=VerificationContract("accuracy")
            ),
            simulated=True,
        )
        c.start()
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
