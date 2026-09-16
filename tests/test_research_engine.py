"""Exercise research scheduling through ARealAgentRuntime with mock inference."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from areal_rsi.api.base import AgentState
from areal_rsi.api.research import ResearchLimits, ResearchReport, TaskBrief
from areal_rsi.coordination.actions import ResearchActions
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.coordination.engine import ResearchEngine, ToolSet
from areal_rsi.modules.responses import (
    EventSink,
    ResponsesConfig,
    ResponsesRuntime,
    ToolCall,
    ToolResult,
)
from areal_rsi.modules.runtime_research import ResearchRuntime

Json = dict[str, Any]


def call(name: str, key: str) -> Json:
    return {"type": "function_call", "name": name, "call_id": key, "arguments": "{}"}


def packet(*output: Json) -> Json:
    return {
        "id": "response_mock",
        "status": "completed",
        "output": list(output),
        "usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30},
    }


def fields() -> Json:
    return {"message": "Checked the runner interface; GPU performance remains untested."}


def definitions(*names: str) -> list[Json]:
    return [
        {
            "type": "function",
            "name": name,
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            "strict": True,
        }
        for name in names
    ]


class RoutedTransport:
    def __init__(self, respond: Callable[[str, int, Json], Awaitable[Json]]):
        self.respond = respond
        self.counts: dict[str, int] = {}
        self.payloads: list[Json] = []

    async def request(self, config: ResponsesConfig, payload: Json, event_sink: EventSink) -> Json:
        snapshots = [
            item["content"].split("\n", 1)[1]
            for item in payload["input"]
            if item.get("role") == "developer"
            and item.get("content", "").startswith("运行时状态快照")
        ]
        if snapshots:
            state = json.loads(snapshots[-1])["authoritative_state"]
        else:
            state = json.loads(payload["input"][0]["content"])["authoritative_state"]
        agent_id = state["agent_id"]
        index = self.counts.get(agent_id, 0)
        self.counts[agent_id] = index + 1
        self.payloads.append(payload)
        return await self.respond(agent_id, index, payload)


class EngineTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = ResponsesConfig(base_url="https://example.test", api_key="test-secret")

    def runtime(self, transport: RoutedTransport) -> ResearchRuntime:
        return ResearchRuntime(ResponsesRuntime(self.config, transport))

    def coordinator(
        self, limits: ResearchLimits | None = None, *, start: bool = True
    ) -> Coordinator:
        c = Coordinator.create(
            self.root,
            TaskBrief(
                "Research an open problem",
                "A baseline is optional",
                limits or ResearchLimits(max_agents=2),
            ),
        )
        if start:
            c.start()
        return c

    def tools(self, c: Coordinator, agent_id: str) -> ToolSet:
        async def handle(item: ToolCall) -> ToolResult:
            if item.name == "report":
                return ToolResult(c.report(agent_id, key=item.call_id, fields=fields()), stop=True)
            c.wait(agent_id, "Wait for a relevant message")
            return ToolResult("waiting", stop=True)

        return ToolSet(definitions("report", "wait"), handle)

    async def test_peer_origin_is_visible_on_wake_and_during_turn(self) -> None:
        c = self.coordinator()
        c.begin_turn("rsi")
        first = c.spawn(key="first", direction="材料方法", instruction="检验材料")
        second = c.spawn(key="second", direction="独立方法", instruction="独立检验")
        for spawned in (first, second):
            c.begin_turn(spawned["agent_id"])
            c.end_turn(spawned["agent_id"])
        c.end_turn("rsi")
        first_id = c.enqueue(first["agent_id"], "rsi", "我的实现未通过", key="first-update")
        second_id: str | None = None

        async def respond(agent_id: str, index: int, payload: Json) -> Json:
            nonlocal second_id
            self.assertEqual(agent_id, "rsi")
            latest = next(item for item in reversed(payload["input"]) if item.get("role") == "user")
            if index == 2:
                self.assertEqual(latest["content"], "继续研究这些材料")
                return packet(call("wait", "user-wait"))
            delivery = json.loads(latest["content"])
            expected = first if index == 0 else second
            self.assertEqual(delivery["sender"], expected["agent_id"])
            self.assertEqual(delivery["recipient"], "rsi")
            self.assertEqual(delivery["sender_direction"], c.actor(expected["agent_id"]).direction)
            self.assertEqual(delivery["message_id"], first_id if index == 0 else second_id)
            if index == 0:
                self.assertEqual(delivery["text"], "我的实现未通过")
                second_id = c.enqueue(
                    second["agent_id"], "rsi", "我的实现还未运行", key="second-update"
                )
                return packet(call("continue", "continue"))
            self.assertEqual(delivery["text"], "我的实现还未运行")
            return packet(call("wait", "peer-wait"))

        async def handle(item: ToolCall) -> ToolResult:
            if item.name == "wait":
                c.wait("rsi", "等待具体实验")
                return ToolResult("waiting", stop=True)
            return ToolResult("continue")

        transport = RoutedTransport(respond)
        engine = ResearchEngine(
            c,
            self.runtime(transport),
            lambda agent: ToolSet(definitions("continue", "wait"), handle),
        )
        await engine.run_until_idle(timeout=3)
        self.assertEqual(transport.counts, {"rsi": 2})
        self.assertEqual(c.state["delivered"].count(first_id), 1)
        self.assertEqual(c.state["delivered"].count(second_id), 1)
        # A subsequent real user request retains its literal instruction.
        c.enqueue("user", "rsi", "继续研究这些材料", key="user-update")
        await engine.run_until_idle(timeout=3)
        self.assertEqual(transport.counts, {"rsi": 3})

    async def test_peer_update_reaches_active_research_without_reassigning(self) -> None:
        c = self.coordinator()
        c.begin_turn("rsi")
        source = c.spawn(key="component", direction="组件", instruction="验证组件")["agent_id"]
        recipient = c.spawn(key="combine", direction="整合", instruction="验证整体产物")["agent_id"]
        c.begin_turn(source)
        c.end_turn(source)
        c.end_turn("rsi")
        actions = ResearchActions(c)

        async def respond(agent_id: str, index: int, payload: Json) -> Json:
            if agent_id == "rsi":
                return packet(call("wait", f"controller-wait-{index}"))
            self.assertEqual(agent_id, recipient)
            if index == 0:
                sent = await actions.handler(source)(
                    ToolCall(
                        "share_update",
                        {"agent_ids": [recipient], "message": "组件可复用", "references": []},
                        "ready",
                    )
                )
                self.assertEqual(sent.output["deliveries"][0]["status"], "sent")
                return packet(call("read", "read"))
            update = next(x for x in reversed(payload["input"]) if x.get("role") == "user")
            self.assertEqual(json.loads(update["content"])["sender"], source)
            self.assertIn("不是新的阶段指令", update["content"])
            self.assertEqual(len(c.actor(recipient).iterations), 1)
            return packet(call("report", "report"))

        def toolset(agent_id: str) -> ToolSet:
            async def handle(item: ToolCall) -> ToolResult:
                if item.name == "read":
                    return ToolResult("材料已读")
                if item.name == "report":
                    return ToolResult(
                        c.report(agent_id, key=item.call_id, fields={"message": "已整合验证"}),
                        stop=True,
                    )
                c.wait(agent_id, "等待研究结果")
                return ToolResult("waiting", stop=True)

            return ToolSet(definitions("read", "report", "wait"), handle)

        transport = RoutedTransport(respond)
        engine = ResearchEngine(c, self.runtime(transport), toolset)
        await engine.run_until_idle(timeout=3)
        self.assertEqual(transport.counts[recipient], 2)
        self.assertEqual(c.actor(recipient).wait_reason, "rsi_reply")
        await actions.handler(source)(
            ToolCall(
                "share_update",
                {"agent_ids": [recipient], "message": "补充已归档", "references": []},
                "after-report",
            )
        )
        await engine.run_until_idle(timeout=3)
        self.assertEqual(transport.counts[recipient], 2)
        self.assertEqual(len(c.actor(recipient).iterations), 1)

    async def test_queued_updates_wait_for_next_assignment_after_report(self) -> None:
        c = self.coordinator()
        c.begin_turn("rsi")
        source = c.spawn(key="source", direction="组件", instruction="验证组件")["agent_id"]
        recipient = c.spawn(key="recipient", direction="整合", instruction="完成初始阶段")[
            "agent_id"
        ]
        c.begin_turn(source)
        c.end_turn(source)
        c.end_turn("rsi")
        updates: list[str] = []
        phase = 0

        async def respond(agent_id: str, index: int, payload: Json) -> Json:
            if agent_id == "rsi":
                return packet(call("wait", f"controller-{index}"))
            self.assertEqual(agent_id, recipient)
            if index == 0:
                # Arrive after request assembly, immediately before its report.
                updates.extend([
                    c.enqueue(source, recipient, "组件后续证据", key="late-peer"),
                    c.enqueue("system", recipient, "先前作业完成", key="late-job"),
                ])
            else:
                self.assertEqual(phase, 1, "A queued update restarted a completed stage")
                self.assertEqual(index, 1)
                user_items = [item for item in payload["input"] if item.get("role") == "user"]
                deliveries = [json.loads(item["content"]) for item in user_items]
                next_task = next(i for i, item in enumerate(deliveries)
                                 if "验证下一阶段" in item.get("text", ""))
                for identifier in updates:
                    observed = next(i for i, item in enumerate(deliveries)
                                    if item.get("message_id") == identifier)
                    self.assertLess(next_task, observed)
                self.assertEqual(len(c.actor(recipient).iterations), 2)
            return packet(call("report", f"report-{index}"))

        transport = RoutedTransport(respond)
        engine = ResearchEngine(
            c, self.runtime(transport), lambda agent: self.tools(c, agent)
        )
        await engine.run_until_idle(timeout=3)
        self.assertEqual(transport.counts[recipient], 1)
        self.assertEqual(c.actor(recipient).wait_reason, "rsi_reply")
        self.assertEqual(c.state["inboxes"][recipient], updates)
        self.assertTrue(all(identifier not in c.state["delivered"] for identifier in updates))

        # Restart retains both the completed-stage barrier and undelivered facts.
        recovered = Coordinator(c.store)
        self.assertNotIn(recipient, recovered.pending())
        phase = 1
        recovered.assign(recipient, key="next-stage", instruction="验证下一阶段")
        engine = ResearchEngine(
            recovered, self.runtime(transport),
            lambda agent: self.tools(recovered, agent),
        )
        await engine.run_until_idle(timeout=3)
        self.assertEqual(transport.counts[recipient], 2)
        self.assertEqual(recovered.actor(recipient).wait_reason, "rsi_reply")
        for identifier in updates:
            self.assertEqual(recovered.state["delivered"].count(identifier), 1)
        self.assertEqual(recovered.state["inboxes"][recipient], [])

    async def test_reported_stage_can_finish_an_interrupted_active_turn(self) -> None:
        c = self.coordinator()
        recipient = c.spawn(key="recipient", direction="研究", instruction="完成阶段")[
            "agent_id"
        ]
        original, _ = c.begin_turn(recipient)
        c.enqueue("system", recipient, "晚到作业结果", key="late-job")
        c.report(recipient, key="report", fields=fields())
        # Crash after report publication, before acknowledging the active turn.
        recovered = Coordinator(c.store)
        self.assertIn(recipient, recovered.pending())
        message, resumed = recovered.begin_turn(recipient)
        self.assertTrue(resumed)
        self.assertEqual(message.message_id, original.message_id)
        recovered.report(recipient, key="report", fields=fields())
        recovered.end_turn(recipient)
        self.assertNotIn(recipient, recovered.pending())
        self.assertEqual(recovered.actor(recipient).wait_reason, "rsi_reply")
        self.assertEqual(len(recovered.store.records("reports", ResearchReport)), 1)

    async def test_job_feedback_reaches_research_while_processing_a_peer_update(self) -> None:
        c = self.coordinator()
        c.begin_turn("rsi")
        source = c.spawn(key="source", direction="组件", instruction="研究组件")["agent_id"]
        recipient = c.spawn(key="recipient", direction="整合", instruction="检验整体")["agent_id"]
        for agent_id in (source, recipient):
            c.begin_turn(agent_id)
            c.wait(agent_id, "job:pending")
            c.end_turn(agent_id)
        c.end_turn("rsi")
        c.enqueue(source, recipient, "组件已有新证据", key="peer-update")
        node_id = c.actor(recipient).current_iteration_id
        completion_id: str | None = None

        async def respond(agent_id: str, index: int, payload: Json) -> Json:
            nonlocal completion_id
            if agent_id == "rsi":
                return packet(call("wait", "rsi-wait"))
            self.assertEqual(agent_id, recipient)
            latest = next(item for item in reversed(payload["input"]) if item.get("role") == "user")
            delivery = json.loads(latest["content"])
            if index == 0:
                self.assertEqual(delivery["sender"], source)
                # The subscribed job finishes after a peer has woken its owner.
                completion_id = c.enqueue(
                    "system", recipient, "作业已完成：整体检验失败", key="job:pending"
                )
                return packet(call("read", "read"))
            self.assertEqual(index, 1)
            self.assertEqual(delivery["sender"], "system")
            self.assertEqual(delivery["message_id"], completion_id)
            self.assertIn("整体检验失败", delivery["text"])
            self.assertEqual(c.actor(recipient).current_iteration_id, node_id)
            return packet(call("report", "report"))

        def toolset(agent_id: str) -> ToolSet:
            async def handle(item: ToolCall) -> ToolResult:
                if item.name == "read":
                    return ToolResult("已读组件证据")
                if item.name == "report":
                    return ToolResult(
                        c.report(agent_id, key=item.call_id, fields={"message": "整体仍需修复"}),
                        stop=True,
                    )
                c.wait(agent_id, "等待研究结果")
                return ToolResult("waiting", stop=True)

            return ToolSet(definitions("read", "report", "wait"), handle)

        transport = RoutedTransport(respond)
        engine = ResearchEngine(c, self.runtime(transport), toolset)
        await engine.run_until_idle(timeout=3)
        self.assertEqual(transport.counts[recipient], 2)
        self.assertEqual(c.state["delivered"].count(completion_id), 1)
        self.assertNotIn(completion_id, c.state["inboxes"][recipient])
        self.assertEqual(c.actor(recipient).wait_reason, "rsi_reply")
        # A later scheduler pass must not replay the completion as a new turn.
        await engine.run_until_idle(timeout=3)
        self.assertEqual(transport.counts[recipient], 2)

    async def test_runtime_budget_tracks_wall_clock_across_waits_and_pause(self) -> None:
        c = self.coordinator(ResearchLimits(max_wall_seconds=1800))
        started = datetime.fromisoformat(c.state["started_at"]).timestamp()
        clock = [started + 1200]
        snapshots: list[Json] = []

        async def respond(agent_id: str, index: int, payload: Json) -> Json:
            snapshot = next(
                item
                for item in reversed(payload["input"])
                if item.get("role") == "developer"
                and item.get("content", "").startswith("运行时状态快照")
            )
            snapshots.append(
                json.loads(snapshot["content"].split("\n", 1)[1])["authoritative_state"]
            )
            return packet(call("wait", f"wait_{index}"))

        transport = RoutedTransport(respond)
        engine = ResearchEngine(
            c,
            self.runtime(transport),
            lambda agent: self.tools(c, agent),
            clock=lambda: clock[0],
        )
        await engine.run_until_idle(timeout=3)
        first = snapshots[-1]["budget"]
        self.assertEqual(first["elapsed_seconds"], 1200)
        self.assertEqual(first["remaining_wall_seconds"], 600)
        self.assertEqual(datetime.fromisoformat(first["observed_at"]).timestamp(), clock[0])
        self.assertEqual(datetime.fromisoformat(first["deadline_at"]).timestamp(), started + 1800)

        c.pause()
        clock[0] += 300
        c.resume()
        c.enqueue("user", "rsi", "Review the remaining work", key="budget-followup")
        await engine.run_until_idle(timeout=3)
        second = snapshots[-1]["budget"]
        self.assertEqual(second["elapsed_seconds"], 1500)
        self.assertEqual(second["remaining_wall_seconds"], 300)
        self.assertEqual(second["deadline_at"], first["deadline_at"])
        self.assertEqual(
            transport.payloads[0]["instructions"], transport.payloads[-1]["instructions"]
        )
        self.assertEqual(c.budget(observed_at=started + 1900)["remaining_wall_seconds"], 0)
        await engine.close()

        unlimited = self.coordinator(ResearchLimits(max_wall_seconds=None), start=False)
        self.assertIsNone(unlimited.budget()["remaining_wall_seconds"])
        self.assertIsNone(unlimited.budget()["deadline_at"])
        self.assertEqual(unlimited.budget()["elapsed_seconds"], 0)

    async def test_pause_cancels_api_and_resume_keeps_original_delivery_and_session(self) -> None:
        c = self.coordinator()
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def respond(agent_id: str, index: int, payload: Json) -> Json:
            if index == 0:
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            return packet(call("wait", f"wait_{index}"))

        transport = RoutedTransport(respond)
        engine = ResearchEngine(
            c, self.runtime(transport), lambda agent: self.tools(c, agent)
        )
        await engine.tick()
        await asyncio.wait_for(started.wait(), 2)
        message_id = c.state["active"]["rsi"]
        c.pause()
        await engine.tick()
        self.assertTrue(cancelled.is_set())
        self.assertEqual(c.state["active"]["rsi"], message_id)
        self.assertEqual(c.actor("rsi").state, AgentState.PAUSED)
        c.resume()
        await engine.run_until_idle(timeout=3)
        self.assertEqual(transport.counts["rsi"], 2)
        self.assertEqual(transport.payloads[0], transport.payloads[1])
        self.assertEqual(
            len([item for item in transport.payloads[-1]["input"] if item.get("role") == "user"]), 1
        )
        self.assertIn(message_id, c.state["delivered"])
        self.assertTrue(
            any(record["reserved_tokens"] > 0 for record in c.state["model_requests"].values())
        )

    async def test_final_reply_archives_report_and_waits_for_next_task_in_same_session(
        self,
    ) -> None:
        c = self.coordinator()
        branch = c.spawn(
            key="inspect",
            direction="Inspect inputs",
            instruction="Inspect the input contract",
            completion_criteria="Report the evidence and limitations",
        )
        researcher = branch["agent_id"]
        effects: list[str] = []

        async def respond(agent_id: str, index: int, payload: Json) -> Json:
            if agent_id == "rsi":
                return packet(call("wait", f"rsi_wait_{index}"))
            if index == 0:
                return packet(call("read", "read_once"))
            if index == 1:
                return packet(
                    {
                        "type": "message",
                        "role": "assistant",
                        "phase": "final_answer",
                        "content": [{"type": "output_text", "text": "The input contract matches."}],
                    }
                )
            self.assertEqual(index, 2)
            context = json.dumps(payload["input"], ensure_ascii=False)
            self.assertIn("The input contract matches.", context)
            self.assertIn("Saved input contract evidence", context)
            self.assertIn("Now check the output contract", context)
            return packet(call("report", "corrected_report"))

        def tools(agent_id: str) -> ToolSet:
            async def handle(item: ToolCall) -> ToolResult:
                if item.name == "read":
                    effects.append(item.call_id)
                    return ToolResult("Saved input contract evidence")
                if item.name == "report":
                    return ToolResult(
                        c.report(agent_id, key=item.call_id, fields=fields()), stop=True
                    )
                c.wait(agent_id, "Await a relevant message")
                return ToolResult("waiting", stop=True)

            return ToolSet(definitions("read", "report", "wait"), handle)

        transport = RoutedTransport(respond)
        engine = ResearchEngine(c, self.runtime(transport), tools)
        await engine.run_until_idle(timeout=3)
        self.assertEqual(c.actor(researcher).state, AgentState.WAITING)
        self.assertEqual(c.actor(researcher).wait_reason, "rsi_reply")
        report = c.store.records("reports", ResearchReport)[0]
        self.assertEqual(report.message, "The input contract matches.")
        session_id = c.actor(researcher).session_id
        count = len(transport.payloads)
        await engine.run_until_idle(timeout=2)
        self.assertEqual(len(transport.payloads), count)
        c.assign(researcher, key="followup", instruction="Now check the output contract")
        self.assertNotIn(researcher, c.state["active"])
        self.assertEqual(len(c.actor(researcher).iterations), 2)
        await engine.run_until_idle(timeout=3)
        self.assertEqual(c.actor(researcher).session_id, session_id)
        self.assertEqual(c.actor(researcher).wait_reason, "rsi_reply")
        self.assertEqual(len(c.actor(researcher).iterations), 2)
        self.assertEqual(len(c.store.records("reports", ResearchReport)), 2)
        self.assertEqual(effects, ["read_once"])
        session = c.store.paths.agent(researcher) / "sessions" / (session_id or researcher)
        saved = json.loads((session / "session.json").read_text())
        self.assertEqual(len(saved["turns"]), 2)
        self.assertNotEqual(saved["turns"][0]["message_id"], saved["turns"][1]["message_id"])
        self.assertNotIn(researcher, c.state.get("turn_failures", {}))
        await engine.close()


if __name__ == "__main__":
    unittest.main()
