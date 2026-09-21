"""Reports and persona-aware advice use the same durable, nonblocking path."""

import asyncio
from pathlib import Path

from test_passk import setup

from areal_rsi.api.base import AgentState
from areal_rsi.api.research import ResearchMessage
from areal_rsi.coordination.collaboration import build_plan, presets
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.modules.agent_runtime import ToolCall


def test_report_and_sharing_preserve_evidence_and_running_attempts(tmp_path: Path) -> None:
    async def scenario() -> None:
        c, _, agents = await setup(tmp_path, 2)
        source, target = agents
        c.state["researcher_profiles"] = {
            source: {"id": "algorithm", "name": "算法研究员", "reminder": "研究计算结构"},
            target: {"id": "memory", "name": "访存研究员", "reminder": "关注数据布局与复用"},
        }
        choice = next(p for p in presets() if p["id"] == "passk_guided_v0213")
        plan = build_plan(choice["mode"], tuple(choice["modules"]))
        actions = plan.actions(c)
        c.begin_turn(source)
        c.begin_turn(target)
        evidence = c.store.put(
            "materials", "experiment", {"agent_id": source, "text": "Verified improvement"}
        )
        result = await actions.handler(source)(
            ToolCall(
                "submit_report",
                {"message": "首次得到可行结果，原因还不清楚。", "references": [evidence.uri]},
                "breakthrough",
            )
        )
        assert not result.stop and result.output["continue_research"]
        assert c.actor(source).state == AgentState.RUNNING
        report = c.state["passk_reports"][source]
        assert report["message"] == "首次得到可行结果，原因还不清楚。"
        assert actions.read_record("rsi", report["uri"])["references"] == [{"uri": evidence.uri}]
        assert any(
            c.store.get("messages", mid, ResearchMessage).sender == source
            for mid in c.state["inboxes"]["rsi"]
        )

        args = {
            "agent_id": target,
            "message": "来源实现通过了验证，尚未在你的布局中验证。",
            "references": [report["uri"], evidence.uri],
            "experience": {"id": "working-method", "kind": "positive", "source_agent_id": source},
        }
        shared = await actions.handler("rsi")(ToolCall("advise_researcher", args, "share"))
        assert not shared.stop and shared.output["status"] == "queued"
        assert c.actor(target).state == AgentState.RUNNING
        message_id = shared.output["message_id"]
        message = c.store.get("messages", message_id, ResearchMessage)
        assert f"来源：{source}（算法研究员）" in message.text
        assert f"接收者：{target}（访存研究员）" in message.text
        assert "关注数据布局与复用" in message.text and args["message"] in message.text
        assert {ref.uri for ref in message.references} == {report["uri"], evidence.uri}
        assert actions.read_record(target, evidence.uri)["text"] == "Verified improvement"
        assert c.state["leader_advice"][target]["message"] == message.text

        restored = Coordinator(c.store)
        retry = await plan.actions(restored).handler("rsi")(
            ToolCall("advise_researcher", args, "share")
        )
        assert retry.output == shared.output
        assert restored.state["inboxes"][target].count(message_id) == 1

        # An observation about the recipient's own work has no peer source.
        own = await plan.actions(restored).handler("rsi")(
            ToolCall(
                "advise_researcher",
                {
                    "agent_id": target,
                    "message": "当前记录还不足以判断原因。",
                    "references": [],
                    "experience": None,
                },
                "observation",
            )
        )
        own_message = c.store.get("messages", own.output["message_id"], ResearchMessage)
        assert "来源：RSI Agent" in own_message.text
        assert source not in own_message.text

    asyncio.run(scenario())
