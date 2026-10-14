"""Run acceptance, continuation and recovery through the real system boundary."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from runtime_fakes import ActionRuntime, CallbackRuntime

from areal_rsi.api.interfaces import ToolCall, TurnResult
from areal_rsi.api.research import TaskBrief, VerificationContract, VerifiedResult
from areal_rsi.infra.coordination.config import ResearchConfig
from areal_rsi.infra.research import ResearchSystem
from areal_rsi.infra.storage.files import now


def config() -> ResearchConfig:
    return ResearchConfig(
        TaskBrief(
            "Write answer", "Create answer.txt containing 42", acceptance=["answer.txt contains 42"]
        ),
        model_settings_pinned=True,
    )


def goal(status: str = "completed", **kwargs: Any) -> dict[str, Any]:
    return dict(
        status=status,
        summary="Wrote and checked answer",
        current_step="Check output",
        next_steps=[],
        acceptance=["answer.txt contains 42"],
        evidence=["Read answer.txt and checked 42"],
        blocker="",
        candidate_id="",
        **kwargs,
    )


async def wait_for(predicate: Any) -> None:
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.01)


def test_leader_continues_without_researchers_and_exports_frozen_artifact(tmp_path: Path) -> None:
    async def scenario() -> None:
        async def respond(session: Path, delivery: dict[str, Any]) -> TurnResult:
            assert delivery["recovery_state"]["agent_id"] == "rsi"
            tools = {t["name"] for t in delivery["tools"]}
            assert "update_goal" in tools
            assert not tools & {"spawn_researcher", "start_researchers", "finish", "submit_report"}
            handler = delivery["handler"]
            if len(runtime.calls) == 1:
                (delivery["workspace"] / "answer.txt").write_text("42")
                updated = await handler(ToolCall("update_goal", goal("active"), "progress"))
                assert "error" not in updated.output
                return TurnResult(
                    session.name, delivery["message_id"], "Continuing", "completed", {}
                )
            assert delivery["recovery_state"]["goal"]["summary"] == "Wrote and checked answer"
            saved = await handler(ToolCall("save_candidate", {"description": "answer"}, "save"))
            args = goal()
            args["candidate_id"] = saved.output["candidate_id"]
            accepted = await handler(ToolCall("update_goal", args, "accept"))
            assert accepted.stop and "error" not in accepted.output
            return TurnResult(session.name, delivery["message_id"], "Done", accepted.reason, {})

        runtime = CallbackRuntime(respond)
        system = ResearchSystem(tmp_path, runtime_factory=lambda _: runtime)
        try:
            run_id = await system.create(config())
            await system.start(run_id)
            await asyncio.wait_for(system.active[run_id].task, 5)
            c = system.coordinator(run_id)
            assert c.state.status == "succeeded", await system.detail(run_id)
            assert not c.store.run().research_agents
            assert len(runtime.calls) == 2
            assert c.state.goal and c.state.goal.status == "completed"
            assert len(list((c.store.root / "acceptance").glob("*.json"))) == 1
            (system.active[run_id].actions.workspace("rsi") / "answer.txt").write_text("changed")
            exported = tmp_path / "exported"
            await system.export(run_id, exported)
            assert (exported / "answer.txt").read_text() == "42"
            with pytest.raises(FileExistsError):
                await system.export(run_id, exported)
        finally:
            await system.close()

    asyncio.run(scenario())


def test_blocked_goal_survives_restart_and_user_input_resumes_leader(tmp_path: Path) -> None:
    async def scenario() -> None:
        async def respond(session: Path, delivery: dict[str, Any]) -> TurnResult:
            handler = delivery["handler"]
            if len(runtime.calls) == 1:
                result = await handler(ToolCall("ask_user", {"question": "Which value?"}, "ask"))
            else:
                assert delivery["user_message"] == "Use 42"
                result = await handler(ToolCall("update_goal", goal(), "done"))
            return TurnResult(session.name, delivery["message_id"], "", result.reason, {})

        runtime = CallbackRuntime(respond)
        system = ResearchSystem(tmp_path, runtime_factory=lambda _: runtime)
        run_id = await system.create(config())
        try:
            await system.start(run_id)
            c = system.coordinator(run_id)
            await wait_for(lambda: c.state.goal and c.state.goal.status == "blocked")
            await system.active[run_id].engine.tick()
            assert len(runtime.calls) == 1
            await system.close()
            system = ResearchSystem(tmp_path, runtime_factory=lambda _: runtime)
            await system.resume(run_id)
            await system.active[run_id].engine.tick()
            assert len(runtime.calls) == 1
            await system.send_message(run_id, "Use 42")
            await asyncio.wait_for(system.active[run_id].task, 5)
            assert system.coordinator(run_id).state.status == "succeeded"
            assert runtime.calls[0][0] == runtime.calls[1][0]
        finally:
            await system.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("formal", [False, True])
def test_acceptance_rejects_missing_evidence_unseen_user_and_unverified_candidate(
    tmp_path: Path,
    formal: bool,
) -> None:
    async def scenario() -> None:
        settings = config()
        if formal:
            settings.brief.verification = VerificationContract(
                "score", target=1, identity={"require_final_confirmation": True}
            )
        system = ResearchSystem(tmp_path, runtime_factory=lambda _: ActionRuntime())
        try:
            run_id = await system.create(settings, simulated=True)
            await system.start(run_id)
            active = system.active[run_id]
            c, handler = active.coordinator, active.actions.handler("rsi")
            await wait_for(lambda: "rsi" in c.state.active)
            args = goal()
            args["evidence"] = []
            denied = await handler(ToolCall("update_goal", args, "empty"))
            assert "evidence" in denied.output["error"]
            candidate = await c.store.snapshot(
                active.actions.workspace("rsi"), agent_id="rsi", description="answer"
            )
            args = goal()
            args["candidate_id"] = candidate.candidate_id
            if formal:
                denied = await handler(ToolCall("update_goal", args, "unchecked"))
                assert "final verification" in denied.output["error"]
                checked = VerifiedResult(
                    "ok",
                    candidate.candidate_id,
                    c.store.contract_digest(),
                    now(),
                    "succeeded",
                    1.0,
                    True,
                    phase="final",
                    simulated=True,
                )
                c.store.record_verification(checked)
            c.user_message("Also explain your test", key="late")
            denied = await handler(ToolCall("update_goal", args, "unseen"))
            assert "pending user" in denied.output["error"]
            assert c.state.status == "running"
            await system.stop(run_id)
            assert c.state.status == "stopped"
        finally:
            await system.close()

    asyncio.run(scenario())


def test_control_ownership_pause_and_saved_delivery(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = ActionRuntime()
        system = ResearchSystem(tmp_path, runtime_factory=lambda _: runtime)
        other = ResearchSystem(tmp_path, runtime_factory=lambda _: runtime)
        try:
            run_id = await system.create(config())
            await system.start(run_id)
            c = system.coordinator(run_id)
            await wait_for(lambda: "rsi" in c.state.active)
            message = c.state.active["rsi"]
            before = c.path.read_bytes()
            with pytest.raises(ValueError, match="Another live process"):
                await other.stop(run_id)
            assert c.path.read_bytes() == before
            await system.pause(run_id)
            assert c.state.status == "paused"
            assert c.state.active["rsi"] == message
            await system.close()
            await other.resume(run_id)
            assert other.coordinator(run_id).state.active["rsi"] == message
            await other.stop(run_id)
        finally:
            await system.close()
            await other.close()

    asyncio.run(scenario())


def test_acceptance_decision_recovers_after_view_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from areal_rsi.api.research import GoalContext
    from areal_rsi.infra.coordination.coordinator import Coordinator
    from areal_rsi.infra.coordination.policies.goal import GoalActions

    async def scenario() -> None:
        c = Coordinator.create(tmp_path, config().brief)
        c.state.goal = GoalContext()
        c.start()
        c.begin_turn("rsi")
        actions = GoalActions(c)
        original = c.save

        def crash() -> None:
            if (c.store.root / "acceptance/run.json").exists():
                raise RuntimeError("Crash after durable decision")
            original()

        with monkeypatch.context() as patch:
            patch.setattr(c, "save", crash)
            with pytest.raises(RuntimeError, match="durable decision"):
                await actions.handler("rsi")(ToolCall("update_goal", goal(), "accept"))
        restored = Coordinator(c.store)
        assert restored.state.status == "running"
        assert GoalActions(restored).recover_acceptance()
        assert restored.state.status == "succeeded"
        assert restored.state.goal and restored.state.goal.status == "completed"
        before = (c.store.root / "acceptance/run.json").read_bytes()
        assert GoalActions(restored).recover_acceptance()
        assert (c.store.root / "acceptance/run.json").read_bytes() == before
        assert len(list((c.store.root / "acceptance").glob("*.json"))) == 1
        messages = [e for e in c.store.events() if e.data.get("recipient") == "user"]
        assert len(messages) == 1

    asyncio.run(scenario())


def test_goal_waits_for_real_verifier_and_accepts_only_final_candidate(tmp_path: Path) -> None:
    import sys

    from areal_rsi.infra.execution import ApprovedCommand

    script = tmp_path / "verify.py"
    script.write_text("""import json, sys, time
from pathlib import Path
out, workspace, phase = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
assert (workspace / 'answer.txt').read_text() == '42'
time.sleep(0.1)
record = workspace.parents[2] / 'jobs' / out.name / 'job.json'
meta = json.loads(record.read_text())['spec']['metadata']
(out / 'evaluation.json').write_text(json.dumps(dict(
    status='completed', simulated=False, phase=phase, metric='score', score=1.0,
    protocol_id='fixed', manifest_id=phase, metrics={'score':1.0},
    candidate_id=meta['candidate_id'], candidate_digest=meta['candidate_digest'])))
""")

    async def scenario() -> None:
        settings = config()
        settings.brief.verification = VerificationContract(
            "score",
            target=1,
            identity={"require_final_confirmation": True},
        )
        settings.verifier = ApprovedCommand(
            "verify",
            (sys.executable, str(script), "{output}", "{workspace}", "{phase}"),
            ("output", "workspace", "phase"),
        )
        settings.metadata["verification_identity"] = {
            "adapter": "content",
            "phases": {p: {"protocol_id": "fixed", "manifest_id": p} for p in ("search", "final")},
        }
        candidate_id = ""

        async def respond(session: Path, delivery: dict[str, Any]) -> TurnResult:
            nonlocal candidate_id
            handler = delivery["handler"]
            if len(runtime.calls) == 1:
                delivery["workspace"].joinpath("answer.txt").write_text("42")
                saved = await handler(ToolCall("save_candidate", {"description": "42"}, "save"))
                candidate_id = saved.output["candidate_id"]
                result = await handler(
                    ToolCall(
                        "verify_candidate",
                        {
                            "candidate_id": candidate_id,
                            "phase": "final",
                            "wait": True,
                        },
                        "final",
                    )
                )
                assert result.stop and result.reason == "job", result
            else:
                assert len(runtime.calls) == 2
                assert "job" in delivery["user_message"]
                args = goal()
                args["candidate_id"] = candidate_id
                result = await handler(ToolCall("update_goal", args, "accept"))
                assert "error" not in result.output, result
            return TurnResult(session.name, delivery["message_id"], "", result.reason, {})

        runtime = CallbackRuntime(respond)
        app = ResearchSystem(tmp_path / "data", runtime_factory=lambda _: runtime)
        try:
            run_id = await app.create(settings)
            await app.start(run_id)
            active = app.active[run_id]
            active.execution.inline_wait_seconds = 0
            await asyncio.wait_for(active.task, 8)
            c = app.coordinator(run_id)
            assert c.state.status == "succeeded", await app.detail(run_id)
            assert len(active.execution.jobs) == 1
            assert c.store.run().final_result_ref
            assert c.selected_candidate_id == candidate_id
        finally:
            await app.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["passk", "pass1", "collaborative", "agent_team"])
def test_only_explicit_current_modes_are_executable(mode: str) -> None:
    from areal_rsi.infra.coordination.config import config_from_dict

    with pytest.raises(ValueError):
        config_from_dict({**config().public(), "mode": mode})
