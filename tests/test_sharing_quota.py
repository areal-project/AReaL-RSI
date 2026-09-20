from __future__ import annotations

import asyncio
from pathlib import Path

from test_passk import ScriptedRuntime, setup

from areal_rsi.coordination.collaboration import DEFAULT_MODULES, build_plan
from areal_rsi.coordination.engine import ToolSet
from areal_rsi.coordination.sharing_quota import SharingQuotaActions, apply
from areal_rsi.modules.agent_runtime import ToolCall


def test_quota_corrections_and_indirect_forwarding(tmp_path: Path) -> None:
    async def scenario() -> None:
        c, _, agents = await setup(tmp_path, 4)
        actions = SharingQuotaActions(c)
        source, recipient, other, _ = agents
        c.store.put("executions", "source-job", {"agent_id": source, "job_id": "source-job"})
        proof = c.store.put(
            "materials",
            "source-finding",
            {"agent_id": "rsi", "job_id": "source-job", "text": "Read source output"},
        )
        experience = {
            "id": "shape-condition",
            "source_agent_id": source,
            "conclusion": "A local finding",
            "conditions": "One measured shape",
            "parents": [],
        }
        args = {
            "agent_id": recipient,
            "message": "Check whether this applies",
            "references": [proof.uri],
            "experience": experience,
        }
        first = await actions.handler("rsi")(ToolCall("advise_researcher", args, "first"))
        assert first.output["status"] == "queued"
        corrected = await actions.handler("rsi")(
            ToolCall("advise_researcher", {**args, "message": "A correction"}, "correct")
        )
        assert corrected.output["status"] == "queued"
        count = len(list((c.store.root / "messages").glob("*.json")))
        denied = await actions.handler("rsi")(
            ToolCall("advise_researcher", {**args, "agent_id": other}, "second-recipient")
        )
        assert "不能增加接收者" in denied.output["error"]
        assert len(list((c.store.root / "messages").glob("*.json"))) == count
        retold = c.store.put(
            "materials", "retold", {"agent_id": recipient, "text": "From source's finding"}
        )
        indirect = await actions.handler("rsi")(
            ToolCall(
                "advise_researcher",
                {
                    "agent_id": other,
                    "message": "Retold",
                    "references": [retold.uri],
                    "experience": {
                        **experience,
                        "id": "extension",
                        "source_agent_id": recipient,
                        "parents": [experience["id"]],
                    },
                },
                "indirect",
            )
        )
        assert "不能增加接收者" in indirect.output["error"]
        assert "extension" not in c.state["shared_experiences"]
        unmarked = await actions.handler("rsi")(
            ToolCall("advise_researcher", {**args, "experience": None}, "unmarked")
        )
        assert "填写 experience" in unmarked.output["error"]
        direct = await actions.handler("rsi")(
            ToolCall(
                "advise_researcher",
                {
                    "agent_id": source,
                    "message": "Investigate your own result",
                    "references": [proof.uri],
                    "experience": None,
                },
                "independent",
            )
        )
        assert direct.output["status"] == "queued"
        assert c.state["shared_experiences"][experience["id"]]["recipient"] == recipient
        assert "latest_report" not in next(
            row for row in actions.state(other)["researchers"] if row["agent_id"] == source
        )
        # The reduced status view does not prevent explicit current-run evidence reads.
        assert actions.read_record(other, proof.uri)["job_id"] == "source-job"
        plan = apply(build_plan("passk", DEFAULT_MODULES))
        engine = plan.engine(c, ScriptedRuntime(), lambda aid: ToolSet([], actions.handler(aid)))
        assert engine._recovery_state("rsi")["shared_experiences"][0]["recipient"] == recipient
        c.stop_agent(other, "budget exhausted")
        stopped = await actions.handler("rsi")(
            ToolCall(
                "advise_researcher",
                {"agent_id": other, "message": "Too late", "references": [], "experience": None},
                "stopped-recipient",
            )
        )
        assert "已经停止" in stopped.output["error"]

    asyncio.run(scenario())
