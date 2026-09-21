"""Counterexamples must affect selection without rewriting trusted measurements."""

import asyncio
import sys
from pathlib import Path
from typing import Literal
from unittest.mock import patch

import pytest

from areal_rsi.api.base import RecordRef
from areal_rsi.api.research import ResearchMessage, TaskBrief, VerificationContract, VerifiedResult
from areal_rsi.base.storage import now
from areal_rsi.coordination.actions import ResearchActions
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.coordination.jobs import ResearchExecution
from areal_rsi.coordination.views import record_preview
from areal_rsi.execution import ApprovedCommand
from areal_rsi.modules.responses import ToolCall


@pytest.mark.parametrize("direction,withdrawer", [("maximize", "rsi"), ("minimize", "research")])
def test_withdrawal_survives_crash_and_excludes_identical_artifacts(
    tmp_path: Path, direction: Literal["maximize", "minimize"], withdrawer: str
) -> None:
    async def scenario() -> None:
        contract = VerificationContract("quality", direction=direction)
        c = Coordinator.create(
            tmp_path, TaskBrief("Improve a data product", "", verification=contract)
        )
        c.start()
        actions = ResearchActions(c)
        workspace = tmp_path / "data"
        workspace.mkdir()
        candidates = []
        results = []
        for name, body, score in (
            ("valid", b"\x00\xffvalid", 0.5),
            ("defective", b"\x00\xffdefective", 0.9 if direction == "maximize" else 0.1),
        ):
            (workspace / "data.bin").write_bytes(body)
            candidate = await c.store.snapshot(workspace, agent_id="rsi", description=name)
            result = VerifiedResult(
                name,
                candidate.candidate_id,
                c.store.contract_digest(),
                now(),
                "succeeded",
                score,
                True,
            )
            c.store.record_verification(result)
            c.store.consider_best(name)
            candidates.append(candidate)
            results.append(result)
        bad = candidates[1]
        duplicate = await c.store.snapshot(
            workspace, agent_id="rsi", description="Same bytes, new identifier"
        )
        run = c.store.run()
        assert run.best_result_ref is not None
        assert c.store.public_record(run.best_result_ref)["candidate_id"] == bad.candidate_id
        c.state["selected_candidate_id"] = bad.candidate_id
        c.save()
        agent = c.spawn(
            key="repair", direction="Repair", instruction="Investigate the counterexample"
        )["agent_id"]
        actor = agent if withdrawer == "research" else "rsi"
        evidence = c.store.put(
            "materials",
            "counterexample",
            {"agent_id": actor, "description": "Fails the allowed empty-input case"},
        )
        tool = ToolCall(
            "withdraw_candidate",
            {
                "candidate_id": bad.candidate_id,
                "reason": "An allowed input produces an invalid result",
                "references": [evidence.uri],
            },
            "withdraw",
        )
        assert "error" in (await actions.handler("interaction")(tool)).output
        if withdrawer == "research":
            private = c.store.put("materials", "private", {"agent_id": "interaction"})
            denied = await actions.handler(agent)(
                ToolCall(
                    "withdraw_candidate",
                    {**tool.arguments, "references": [private.uri]},
                    "private-evidence",
                )
            )
            assert "error" in denied.output
            assert not c.store.withdrawals()
        with (
            patch.object(c.store, "save_run", side_effect=RuntimeError("process lost")),
            pytest.raises(RuntimeError, match="process lost"),
        ):
            await actions.handler(actor)(tool)

        recovered = ResearchActions(Coordinator(c.store))
        withdrawal = await recovered.handler(actor)(tool)
        assert withdrawal.output["withdrawn"]
        assert withdrawal.output["best"]["candidate_id"] == candidates[0].candidate_id
        assert recovered.c.state["selected_candidate_id"] is None
        assert c.store.get("verifications", "defective", VerifiedResult) == results[1]
        assert c.store.is_withdrawn(duplicate.candidate_id)
        assert not c.store.consider_best("defective")
        assert (await recovered.handler(actor)(tool)).output == withdrawal.output
        assert len(c.store.withdrawals()) == 1
        assert len([e for e in c.store.events() if e.kind == "candidate_withdrawn"]) == 1
        # Withdrawal metadata uses the candidate's storage key, but must not
        # invalidate the existing short reference to the immutable candidate.
        assert recovered.read_record("rsi", bad.candidate_id)["content"]["digest"] == (
            bad.content.digest
        )
        withdrawal_ref = RecordRef(withdrawal.output["uri"])
        withdrawal_record = recovered.read_record("rsi", withdrawal_ref.uri)
        assert withdrawal_record["agent_id"] == actor
        notifications = [
            message
            for message in c.store.records("messages", ResearchMessage)
            if message.recipient == "rsi" and withdrawal_ref in message.references
        ]
        assert len(notifications) == (1 if withdrawer == "research" else 0)
        if notifications:
            notification = notifications[0]
            assert notification.sender == agent
            assert bad.candidate_id in notification.text
            assert tool.arguments["reason"] in notification.text
            assert notification.message_id in recovered.c.state["inboxes"]["rsi"]
            assert evidence in notification.references
        preview = record_preview(withdrawal_record, withdrawal_ref)
        assert preview["record_id"] == "withdrawals/" + bad.candidate_id
        assert recovered.read_record("rsi", preview["record_id"]) == withdrawal_record
        assert (
            recovered.read_record(agent, withdrawal.output["uri"])["reason"]
            == tool.arguments["reason"]
        )
        refused = await recovered.handler("rsi")(
            ToolCall(
                "finish",
                {"summary": "Done", "candidate_id": duplicate.candidate_id},
                "finish-withdrawn",
            )
        )
        assert "withdrawn" in refused.output["error"]
        # A withdrawn artifact remains available for repair and forensic comparison.
        imported = await recovered.handler(agent)(
            ToolCall(
                "import_candidate",
                {"candidate_id": bad.candidate_id, "destination": "repair"},
                "import",
            )
        )
        assert (
            Path(imported.output["directory"]) / "data.bin"
        ).read_bytes() == b"\x00\xffdefective"
        assert recovered.read_record(agent, bad.candidate_id)["candidate_id"] == bad.candidate_id
        await recovered.handler("rsi")(
            ToolCall(
                "withdraw_candidate",
                {**tool.arguments, "candidate_id": candidates[0].candidate_id},
                "withdraw-last",
            )
        )
        assert c.store.run().best_result_ref is None
        (workspace / "data.bin").write_bytes(b"\x00\xffrepaired")
        repaired = await c.store.snapshot(workspace, agent_id="rsi", description="Repaired")
        repaired_result = VerifiedResult(
            "repaired",
            repaired.candidate_id,
            c.store.contract_digest(),
            now(),
            "succeeded",
            0.6,
            True,
        )
        c.store.record_verification(repaired_result)
        assert c.store.consider_best("repaired")

    asyncio.run(scenario())


def test_late_final_result_does_not_reinstate_withdrawn_artifact(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Coordinator.create(
            tmp_path,
            TaskBrief("Improve a report", "", verification=VerificationContract("quality")),
        )
        c.start()
        workspace = tmp_path / "report"
        workspace.mkdir()
        (workspace / "report.txt").write_text("A report with a disproven claim")
        candidate = await c.store.snapshot(workspace, agent_id="rsi", description="Report")
        runner = ResearchExecution(
            ResearchActions(c),
            verifier=ApprovedCommand("verify", (sys.executable, "-c", "pass")),
            read_verification=lambda job, artifact, phase: VerifiedResult(
                job.job_id,
                artifact.candidate_id,
                c.store.contract_digest(),
                now(),
                "succeeded",
                1.0,
                True,
                phase="final",
            ),
        )
        try:
            submitted = await runner.submit(
                "rsi",
                ToolCall(
                    "verify_candidate",
                    {"candidate_id": candidate.candidate_id, "phase": "final"},
                    "started-before-withdrawal",
                ),
            )
            c.store.withdraw_candidate(
                candidate.candidate_id, "The central claim has a counterexample", ()
            )
            async with asyncio.timeout(10):
                while await runner.poll():
                    await asyncio.sleep(0.05)
            assert c.store.get("verifications", submitted.output["job_id"], VerifiedResult).passed
            assert c.store.run().final_result_ref is None
            with pytest.raises(ValueError, match="Withdrawn"):
                await runner.submit(
                    "rsi",
                    ToolCall(
                        "verify_candidate",
                        {"candidate_id": candidate.candidate_id, "phase": "final"},
                        "already-withdrawn",
                    ),
                )
        finally:
            runner.close(cancel=True)

    asyncio.run(scenario())


def test_batch_withdrawal_is_atomic_before_selection_and_survives_stop(tmp_path: Path) -> None:
    async def scenario():
        c = Coordinator.create(
            tmp_path, TaskBrief("test", "", verification=VerificationContract("quality"))
        )
        workspace = tmp_path / "input"
        workspace.mkdir()
        candidates = []
        for i in range(3):
            (workspace / "source.txt").write_text(str(i))
            item = await c.store.snapshot(workspace, agent_id="rsi", description=str(i))
            candidates.append(item.candidate_id)
            c.store.record_verification(
                VerifiedResult(
                    str(i),
                    item.candidate_id,
                    c.store.contract_digest(),
                    now(),
                    "succeeded",
                    float(i),
                    True,
                )
            )
            c.store.consider_best(str(i))
        with pytest.raises(FileNotFoundError):
            c.store.withdraw_candidates(
                [candidates[1], "missing"], "counterexample", (), agent_id="rsi"
            )
        assert not c.store.withdrawals()
        with patch.object(c.store, "save_run", side_effect=RuntimeError("crash after commit")):
            with pytest.raises(RuntimeError):
                c.store.withdraw_candidates(candidates[1:], "counterexample", (), agent_id="rsi")
        assert all(c.store.is_withdrawn(key) for key in candidates[1:])
        c.stop("budget expired")
        recovered = Coordinator(c.store)
        recovered.store.withdraw_candidates(candidates[1:], "counterexample", (), agent_id="rsi")
        best = recovered.store.run().best_result_ref
        assert recovered.store.public_record(best)["candidate_id"] == candidates[0]
        assert len(recovered.store.withdrawals()) == 2

    asyncio.run(scenario())
