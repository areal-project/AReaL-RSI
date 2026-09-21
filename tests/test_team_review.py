"""A quiet leader is nudged without polling models or interrupting researchers."""

import asyncio
import time
from pathlib import Path

from test_passk import ScriptedRuntime, configuration

from areal_rsi.base.storage import encode
from areal_rsi.coordination.collaboration import build_plan
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.coordination.engine import ToolSet
from areal_rsi.coordination.passk import PassKActions, launch
from areal_rsi.coordination.team_review import TeamReviewEngine


def test_review_is_periodic_coalesced_and_durable(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Coordinator.create(tmp_path, configuration(2, seconds=10000).brief)
        c.state["mode"] = "passk"
        c.start()
        plan = build_plan(
            "passk", ("leader_guidance", "problem_sharing", "researcher_profiles", "team_review")
        )
        actions = plan.actions(c)
        assert isinstance(actions, PassKActions)
        await launch(c, actions)
        at = time.time()
        engine = plan.engine(
            c, ScriptedRuntime(), lambda a: ToolSet([], actions.handler(a)), clock=lambda: at
        )
        assert isinstance(engine, TeamReviewEngine)
        before = [encode(a) for a in c.store.run().research_agents]
        engine._remind_team_review()
        initial_inbox = list(c.state["inboxes"].get("rsi", []))
        at += 599
        engine._remind_team_review()
        assert c.state["inboxes"].get("rsi", []) == initial_inbox
        at += 1
        engine._remind_team_review()
        first = c.state["team_review"]["message_id"]
        assert first in c.state["inboxes"]["rsi"]
        at += 1200
        engine._remind_team_review()
        assert c.state["team_review"]["message_id"] == first
        assert [encode(a) for a in c.store.run().research_agents] == before
        restored = Coordinator(c.store)
        engine.coordinator = restored
        restored.state["delivered"].append(first)
        engine._remind_team_review()
        second = restored.state["team_review"]["message_id"]
        assert second != first
        restored.state["status"] = "paused"
        at += 1200
        engine._remind_team_review()
        assert restored.state["team_review"]["message_id"] == second

    asyncio.run(scenario())
