from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from test_passk import ScriptedRuntime, configuration

from areal_rsi.api.research import CandidateSnapshot
from areal_rsi.base.storage import write_json
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.coordination.engine import ToolSet
from areal_rsi.coordination.passk import launch
from areal_rsi.coordination.researcher_restart import RestartActions, RestartEngine
from areal_rsi.modules.agent_runtime import ToolCall, TurnResult


def test_restart_waits_for_old_jobs_and_preserves_clock_and_archive(tmp_path: Path) -> None:
    class Runtime(ScriptedRuntime):
        def __init__(self) -> None:
            super().__init__()
            self.sessions: list[tuple[str, Path, str]] = []
            self.closed_sessions: list[Path] = []

        async def turn(self, session_dir: Path, **kwargs: Any) -> TurnResult:
            self.sessions.append(
                (kwargs["recovery_state"]["agent_id"], session_dir, kwargs["user_message"])
            )
            await asyncio.Event().wait()
            raise AssertionError("cancelled before completion")

        async def close_session(self, session_dir: Path) -> None:
            self.closed_sessions.append(session_dir)

    async def scenario() -> None:
        config = configuration(4)
        config.brief.limits.max_wall_seconds = None
        config.brief.limits.max_effective_seconds = 3600
        c = Coordinator.create(tmp_path, config.brief)
        c.state["mode"] = "passk"
        c.start()
        actions = RestartActions(c)
        await launch(c, actions)
        target = c.store.run().research_agents[0].agent_id
        workspace = actions.workspace(target)
        (workspace / "model.py").write_text("historical branch")
        candidate = await c.store.snapshot(workspace, agent_id=target, description="earlier route")
        actions.mark_workspace_candidate(target, candidate.candidate_id)
        (workspace / "model.py").write_text("latest unsaved branch")
        runtime = Runtime()
        engine = RestartEngine(
            c, runtime, lambda aid: ToolSet(actions.definitions(aid), actions.handler(aid))
        )
        try:
            await engine.tick()
            await asyncio.sleep(0.01)
            assert engine.effective is not None
            at = time.time()
            engine.effective.data["periods"] = [[at - 1200, None]]
            engine.effective.data["agents"][target]["started_at"] = at - 1200
            c.save()
            old_session = c.actor(target).session_id
            handoff = await actions.handler("rsi")(
                ToolCall(
                    "advise_researcher",
                    {
                        "agent_id": target,
                        "message": "Revisit the untested historical hypothesis",
                        "references": [],
                    },
                    "handoff",
                )
            )
            c.enqueue("rsi", target, "Obsolete unrelated message", key="obsolete")
            job = {
                "agent_id": target,
                "processed": False,
                "created_at": "2026-09-19T00:00:00+00:00",
            }
            jobs_path = c.store.root / "coordination/jobs.json"
            write_json(jobs_path, {"old-job": job})
            result = await actions.handler("rsi")(
                ToolCall(
                    "restart_researcher",
                    {
                        "agent_id": target,
                        "candidate_id": candidate.candidate_id,
                        "reason": "Repeated trials of an already shared approach",
                        "handoff_message_id": handoff.output["message_id"],
                    },
                    "restart",
                )
            )
            assert result.output["status"] == "queued"
            await engine.tick()
            await engine.tick()
            assert c.actor(target).session_id == old_session
            assert (workspace / "model.py").read_text() == "latest unsaved branch"
            assert target not in engine.active_agents
            job["processed"] = True
            write_json(jobs_path, {"old-job": job})
            await engine.tick()
            await asyncio.sleep(0.01)
            request = c.state["researcher_restarts"][result.output["request_id"]]
            assert request["status"] == "completed"
            assert c.actor(target).session_id != old_session
            assert len(c.store.run().research_agents) == 4
            assert (workspace / "model.py").read_text() == "historical branch"
            assert 1200 <= c.budget()["researchers"][target]["effective_seconds"] < 1210
            archived = c.store.get(
                "candidates", request["archived_candidate_id"], CandidateSnapshot
            )
            destination = tmp_path / "archive-inspection"
            await c.store.content.materialize(archived.content, destination)
            assert (destination / "model.py").read_text() == "latest unsaved branch"
            turns = [session for session in runtime.sessions if session[0] == target]
            assert len(turns) == 2 and turns[0][1] != turns[1][1]
            assert "Revisit the untested historical hypothesis" in turns[1][2]
            assert "Obsolete unrelated message" not in turns[1][2]
            assert turns[0][1] in runtime.closed_sessions
            historical = await actions.handler("rsi")(
                ToolCall(
                    "read_researcher_trace",
                    {"agent_id": target, "session_id": old_session, "offset": -1},
                    "historical-trace",
                )
            )
            assert historical.output["session_id"] == old_session
            wrong_session = await actions.handler("rsi")(
                ToolCall(
                    "read_researcher_trace",
                    {"agent_id": target, "session_id": "unrelated-session", "offset": -1},
                    "wrong-trace",
                )
            )
            assert "不属于当前 run" in wrong_session.output["error"]
            visible = next(
                row for row in actions.state(target)["researchers"] if row["agent_id"] == target
            )
            assert visible["previous_sessions"] == [old_session]
            repeated = await actions.handler("rsi")(
                ToolCall(
                    "restart_researcher",
                    {
                        "agent_id": target,
                        "candidate_id": candidate.candidate_id,
                        "reason": "Again",
                        "handoff_message_id": handoff.output["message_id"],
                    },
                    "restart-again",
                )
            )
            assert "每人一次" in repeated.output["error"]
        finally:
            c.stop("test finished")
            await engine.close()

    asyncio.run(scenario())
