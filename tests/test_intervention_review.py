from __future__ import annotations

import asyncio
from pathlib import Path

from test_passk import ScriptedRuntime, setup

from areal_rsi.coordination.collaboration import DEFAULT_MODULES, build_plan
from areal_rsi.coordination.engine import ToolSet
from areal_rsi.coordination.intervention_review import InterventionReviewActions, apply
from areal_rsi.coordination.sharing_quota import SharingQuotaActions
from areal_rsi.coordination.sharing_quota import apply as apply_quota
from areal_rsi.modules.agent_runtime import ToolCall


def test_decision_and_quota_compose_without_new_permissions(tmp_path: Path) -> None:
    class Combined(InterventionReviewActions, SharingQuotaActions):
        pass

    async def scenario() -> None:
        c, _, agents = await setup(tmp_path, 4)
        actions = Combined(c)
        schema = next(t for t in actions.definitions("rsi") if t["name"] == "advise_researcher")[
            "parameters"
        ]
        assert {"decision", "experience"} <= set(schema["required"])
        assert "wait" not in {t["name"] for t in actions.definitions("rsi")}
        assert "advise_researcher" not in {t["name"] for t in actions.definitions(agents[0])}
        decision = {
            "kind": "hypothesis",
            "problem": "An unresolved cause",
            "evidence": "Two observations",
            "question": "Which explanation fits?",
            "followup": "First advice",
        }
        result = await actions.handler("rsi")(
            ToolCall(
                "advise_researcher",
                {
                    "agent_id": agents[0],
                    "message": "One discriminating experiment",
                    "references": [],
                    "experience": None,
                    "decision": decision,
                },
                "decision",
            )
        )
        assert result.output["status"] == "queued"
        recorded = actions.state("rsi")["researchers"][0]["previous_decision"]
        assert recorded["decision"] == decision
        assert recorded["message_id"] == result.output["message_id"]
        assert actions.read_record("rsi", recorded["uri"])["decision"] == decision
        for base in (
            build_plan("passk", DEFAULT_MODULES),
            apply_quota(build_plan("passk", DEFAULT_MODULES)),
        ):
            plan = apply(base)
            engine = plan.engine(
                c, ScriptedRuntime(), lambda aid: ToolSet([], actions.handler(aid))
            )
            restored = engine._recovery_state("rsi")["researchers"][0]["previous_decision"]
            assert restored["decision"] == decision

    asyncio.run(scenario())
