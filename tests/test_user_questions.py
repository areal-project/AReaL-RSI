"""Direct user questions preserve routing, durable waiting and continuous sessions."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from areal_rsi.api.base import AgentState
from areal_rsi.api.research import ResearchMessage, TaskBrief
from areal_rsi.coordination.config import ResearchConfig
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.coordination.store import ResearchStore
from areal_rsi.modules.responses import ResponsesConfig, ResponsesRuntime
from areal_rsi.research_app import ResearchApp


def test_replies_are_correlated_and_survive_partial_writes_and_restarts(tmp_path: Path) -> None:
    c = Coordinator.create(tmp_path, TaskBrief("Improve", "Inspect and repair access"))
    c.start()
    rsi_question = c.ask_user("rsi", "目标模型返回 401，请检查访问权限。", key="rsi-ask")
    interaction_question = c.ask_user(
        "interaction", "需要说明目前的进展吗？", key="interaction-ask"
    )
    # Older runs persisted only these two fields; recover the author from the message.
    pending_before = [
        {"message_id": q["message_id"], "question": q["question"]}
        for q in c.state["waiting_for_user"]
    ]
    reply_id = c.user_message("权限已修复", key="user-reply", reply_to=rsi_question)
    reply = c.store.get("messages", reply_id, ResearchMessage)
    assert (reply.sender, reply.recipient, reply.reply_to) == ("user", "rsi", rsi_question)
    assert [q["message_id"] for q in c.user_questions()] == [interaction_question]
    assert not c.state["inboxes"].get("interaction")
    # Simulate a stop after writing the reply but before clearing the pending index.
    c.state["waiting_for_user"] = pending_before
    c.save()
    recovered = Coordinator(ResearchStore(tmp_path, c.store.run_id))
    assert [q["message_id"] for q in recovered.summary()["waiting_for_user"]] == [
        interaction_question
    ]
    assert recovered.user_message("权限已修复", key="user-reply") == reply_id
    assert recovered.state["inboxes"]["rsi"].count(reply_id) == 1
    assert [q["message_id"] for q in recovered.state["waiting_for_user"]] == [interaction_question]
    # Replay a partially journaled ask after the user already replied.
    recovered.ask_user("rsi", "目标模型返回 401，请检查访问权限。", key="rsi-ask")
    assert [q["message_id"] for q in recovered.user_questions()] == [interaction_question]
    with pytest.raises(ValueError, match="idempotency"):
        recovered.user_message("不同回答", key="user-reply")
    with pytest.raises(ValueError, match="idempotency"):
        recovered.user_message("权限已修复", key="user-reply", reply_to=interaction_question)
    with pytest.raises(ValueError, match="已回复"):
        recovered.user_message("旧问题", key="stale", reply_to=rsi_question)


def test_default_reply_explicit_message_and_wrong_recipient(tmp_path: Path) -> None:
    c = Coordinator.create(tmp_path, TaskBrief("Study", "Discuss first"))
    with pytest.raises(ValueError, match="尚未开始"):
        c.user_message("开始", key="too-early", recipient="rsi")
    c.start()
    question_id = c.ask_user("rsi", "GPU 无法连接，请恢复访问。", key="ask")
    c.user_message("想了解研究方向", key="general", recipient="interaction")
    assert len(c.user_questions()) == 1
    with pytest.raises(ValueError, match="原提问者"):
        c.user_message("修好了", key="wrong", reply_to=question_id, recipient="interaction")
    with pytest.raises(ValueError, match="不存在"):
        c.user_message("修好了", key="foreign", reply_to="message_other_run")
    c.user_message("已经恢复", key="auto-reply")
    assert not c.user_questions()
    replies = [m for m in c.store.records("messages", ResearchMessage) if m.reply_to == question_id]
    assert len(replies) == 1 and replies[0].recipient == "rsi"
    c.ask_user("rsi", "第二个问题", key="rsi-second")
    c.ask_user("interaction", "另一个问题", key="interaction-second")
    c.user_message("一条新建议", key="ambiguous")
    assert len(c.user_questions()) == 2
    c.stop("User cancelled")
    assert not c.user_questions()
    assert c.store.get("messages", question_id, ResearchMessage).recipient == "user"
    with pytest.raises(ValueError, match="研究已结束"):
        c.user_message("迟到的修复反馈", key="late")
    with pytest.raises(ValueError, match="研究已结束"):
        c.ask_user("rsi", "迟到的问题", key="late-question")


class QuestionTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def request(self, config: Any, payload: Any, sink: Any) -> dict[str, Any]:
        snapshot = payload["instructions"].split("当前 run 的系统状态：\n", 1)[1]
        agent_id = json.JSONDecoder().raw_decode(snapshot)[0]["agent_id"]
        self.calls.append((agent_id, payload))
        index = len(self.calls)
        output = (
            [
                {
                    "type": "function_call",
                    "name": "ask_user",
                    "call_id": "access-question",
                    "arguments": json.dumps({"question": "GPU 连接被拒绝，请恢复权限后告诉我。"}),
                }
            ]
            if index == 1
            else [
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "收到，我会先检查访问是否恢复。"}],
                }
            ]
        )
        return {
            "id": f"response_{index}",
            "status": "completed",
            "output": output,
            "usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30},
        }


def test_rsi_waits_without_calls_and_user_reply_resumes_same_session_after_restart(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        transport = QuestionTransport()
        config = ResearchConfig(TaskBrief("Inspect access", "An external repair may be needed"))

        def make_app() -> ResearchApp:
            return ResearchApp(
                tmp_path,
                config=config,
                runtime_factory=lambda _: ResponsesRuntime(
                    ResponsesConfig(base_url="https://example.test", api_key="test"), transport
                ),
            )

        app = make_app()
        run_id = (await app.create({"simulated": False}))["run_id"]

        async def settle(active_app: ResearchApp, expected: int) -> None:
            for _ in range(300):
                engine = active_app.active[run_id].engine
                if (
                    len(transport.calls) == expected
                    and not engine.active_agents
                    and not engine.coordinator.pending()
                ):
                    return
                await asyncio.sleep(0.01)
            raise AssertionError("Agent did not become idle")

        try:
            await app.call("start", {"run_id": run_id})
            await settle(app, 1)
            c = app.coordinator(run_id)
            session_id = c.actor("rsi").session_id
            question = c.user_questions()[0]
            assert c.actor("rsi").state == AgentState.WAITING
            assert c.actor("rsi").wait_reason == "user"
            for _ in range(3):
                await app.detail(run_id)
                await app.active[run_id].engine.tick()
            assert len(transport.calls) == 1
            await app.close()
            app = make_app()
            await app.call("resume", {"run_id": run_id})
            await asyncio.sleep(0.05)
            assert len(transport.calls) == 1
            await app.call("pause", {"run_id": run_id})
            result = await app.call(
                "chat",
                {
                    "run_id": run_id,
                    "text": "权限已经恢复，请继续",
                    "message_id": "fixed",
                    "reply_to": question["message_id"],
                },
            )
            assert result["message_id"]
            await asyncio.sleep(0.05)
            assert len(transport.calls) == 1
            await app.call("resume", {"run_id": run_id})
            await settle(app, 2)
            detail = await app.detail(run_id)
            assert detail["waiting_for_user"] == []
            assert [agent for agent, _ in transport.calls] == ["rsi", "rsi"]
            assert app.coordinator(run_id).actor("rsi").session_id == session_id
            second_request = transport.calls[1][1]
            assert any(
                item.get("type") == "function_call"
                and item.get("name") == "ask_user"
                and json.loads(item["arguments"])["question"] == question["question"]
                for item in second_request["input"]
            )
            snapshot = json.JSONDecoder().raw_decode(
                second_request["instructions"].split("当前 run 的系统状态：\n", 1)[1]
            )[0]
            assert snapshot["current_message"]["sender"] == "user"
            assert snapshot["current_message"]["reply_to"] == question["message_id"]
            assert detail["messages"][-1]["sender"] == "rsi"
            assert detail["messages"][-1]["reply_to"] == result["message_id"]
            await app.call(
                "chat", {"run_id": run_id, "text": "权限已经恢复，请继续", "message_id": "fixed"}
            )
            await asyncio.sleep(0.05)
            assert len(transport.calls) == 2
        finally:
            await app.close()

    asyncio.run(scenario())
