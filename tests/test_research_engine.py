"""Exercise scheduler decisions with real durable Responses turns and mock inference."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any

from areal_rsi.api.base import AgentState
from areal_rsi.api.research import ResearchLimits, ResearchMessage, ResearchReport, TaskBrief
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.coordination.engine import ResearchEngine, ToolSet
from areal_rsi.coordination.store import ResearchStore
from areal_rsi.modules.responses import (
    EventSink,
    ResponsesConfig,
    ResponsesError,
    ResponsesRuntime,
    ToolCall,
    ToolResult,
)

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
    return {
        "task_status": "Completed the assigned check",
        "work": "Compared the configured inputs with the runner contract",
        "problems": "The GPU is not provisioned; runtime measurement was not attempted",
        "results": "The runner contract supports the required invocation",
        "interpretation": "Static interface compatibility only; performance remains untested",
        "next_steps": "Use one simulated job to check completion delivery and cancellation",
    }


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
        if "当前 run 的系统状态：\n" in payload["instructions"]:
            raw = payload["instructions"].split("当前 run 的系统状态：\n", 1)[1]
            state = json.JSONDecoder().raw_decode(raw)[0]
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

    async def test_report_reaches_active_rsi_once_and_does_not_replace_user_reply(self) -> None:
        async def scenario(has_user_update: bool) -> None:
            c = self.coordinator()
            researcher = c.spawn(key="research", direction="A", instruction="检查结果")["agent_id"]
            user_id = (
                c.enqueue("user", "rsi", "请告诉我最新进展", key="user")
                if has_user_update
                else None
            )
            reported = asyncio.Event()
            received: list[str] = []

            async def respond(agent_id: str, index: int, payload: Json) -> Json:
                if agent_id == researcher:
                    return packet(call("report", "report"))
                if index == 0:
                    await reported.wait()  # Report arrives during RSI's model call.
                    return packet(call("inspect", "inspect"))
                self.assertEqual(index, 1, "Report incorrectly woke RSI for a second turn")
                updates = []
                for item in payload["input"]:
                    if item.get("role") != "user":
                        continue
                    try:
                        value = json.loads(item.get("content", ""))
                    except ValueError:
                        continue
                    if isinstance(value, dict) and value.get("sender") == researcher:
                        updates.append(value)
                self.assertEqual(len(updates), 1)
                self.assertIn("报告记录：", updates[0]["text"])
                received.append(updates[0]["message_id"])
                return packet(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "已收到研究结果。"}],
                    }
                )

            def tools(agent_id: str) -> ToolSet:
                regular = self.tools(c, agent_id)

                async def handle(item: ToolCall) -> ToolResult:
                    if item.name == "inspect":
                        return ToolResult({"checked": True})
                    result = await regular.call(item)
                    if item.name == "report":
                        reported.set()
                    return result

                return ToolSet(definitions("inspect", "report", "wait"), handle)

            transport = RoutedTransport(respond)
            engine = ResearchEngine(c, ResponsesRuntime(self.config, transport), tools)
            await engine.run_until_idle(timeout=5)
            self.assertEqual(transport.counts, {"rsi": 2, researcher: 1})
            recovered = Coordinator(ResearchStore(self.root, c.store.run_id))
            self.assertEqual(recovered.state["delivered"].count(received[0]), 1)
            self.assertNotIn("rsi", recovered.pending())
            replies = [
                m
                for m in c.store.records("messages", ResearchMessage)
                if m.sender == "rsi" and m.recipient == "user"
            ]
            if has_user_update:
                self.assertEqual(len(replies), 1)
                self.assertEqual(replies[0].reply_to, user_id)
            else:
                self.assertEqual(replies, [])

        for has_user_update in (False, True):
            with self.subTest(has_user_update=has_user_update):
                await scenario(has_user_update)

    async def test_job_wakeup_retains_current_assignment_after_controller_reload(self) -> None:
        c = self.coordinator()
        instruction = "仅提交一次评测；收到结果后向 RSI 汇报，再等待下一轮任务。"
        criteria = "报告本次结果，不自动提交下一次评测。"
        assignment = c.spawn(
            key="direction",
            direction="改进实现",
            instruction=instruction,
            completion_criteria=criteria,
        )
        researcher = assignment["agent_id"]

        async def respond(agent_id: str, index: int, payload: Json) -> Json:
            if agent_id == "rsi":
                return packet(call("wait", f"rsi_{index}"))
            raw = payload["instructions"].split("当前 run 的系统状态：\n", 1)[1]
            state = json.JSONDecoder().raw_decode(raw)[0]
            task = state["current_assignment"]
            if index < 2:
                self.assertEqual(task["instruction"], instruction)
                self.assertEqual(task["completion_criteria"], criteria)
            else:
                self.assertEqual(task["instruction"], "根据报告继续做代码审查，不提交评测。")
                self.assertNotEqual(task["assignment_id"], first_task_id)
            if index == 1:
                self.assertEqual(state["current_message"]["sender"], "system")
                self.assertEqual(task["assignment_id"], first_task_id)
            return packet(call("wait" if index == 0 else "report", f"research_{index}"))

        transport = RoutedTransport(respond)
        engine = ResearchEngine(c, ResponsesRuntime(self.config, transport), partial(self.tools, c))
        first_task_id = engine._recovery_state(researcher)["current_assignment"]["assignment_id"]
        await engine.run_until_idle(timeout=5)
        c.enqueue("system", researcher, "你订阅的作业已完成，请处理结果。", key="job-completed")
        # Recover from durable records with a job message as the active delivery.
        c = Coordinator(ResearchStore(self.root, c.store.run_id))
        engine = ResearchEngine(c, ResponsesRuntime(self.config, transport), partial(self.tools, c))
        await engine.run_until_idle(timeout=5)
        self.assertEqual(len(c.actor(researcher).iterations), 1)
        self.assertIsNotNone(c.actor(researcher).iterations[0].report_ref)
        c.assign(researcher, key="next", instruction="根据报告继续做代码审查，不提交评测。")
        await engine.run_until_idle(timeout=5)
        self.assertEqual(transport.counts[researcher], 3)

    async def test_unlimited_run_keeps_usage_and_enforces_only_five_researchers(self) -> None:
        limits = ResearchLimits(
            max_agents=5,
            max_nodes=None,
            max_model_calls=None,
            max_total_tokens=None,
            max_wall_seconds=None,
            max_verify_calls=None,
            max_jobs=None,
        )
        c = self.coordinator(limits)
        c.state.update(
            model_calls=1000, total_tokens=20_000_000, started_at="2000-01-01T00:00:00+00:00"
        )
        c.save()
        agents = [
            c.spawn(key=str(i), direction=str(i), instruction="检查证据")["agent_id"]
            for i in range(5)
        ]
        with self.assertRaisesRegex(ValueError, "agent limit"):
            c.spawn(key="sixth", direction="extra", instruction="检查证据")

        async def respond(agent_id: str, index: int, payload: Json) -> Json:
            return packet(call("wait" if agent_id == "rsi" else "report", f"{agent_id}_{index}"))

        transport = RoutedTransport(respond)
        for round_index in range(5):
            c = Coordinator(ResearchStore(self.root, c.store.run_id))
            if round_index:
                for agent in agents:
                    c.assign(agent, key=f"{round_index}:{agent}", instruction="继续检查")
            engine = ResearchEngine(
                c, ResponsesRuntime(self.config, transport), partial(self.tools, c)
            )
            await engine.run_until_idle(timeout=5)
            self.assertEqual(c.state["status"], "running")
        self.assertEqual(sum(len(a.iterations) for a in c.store.run().research_agents), 25)
        self.assertGreater(c.state["model_calls"], 1000)
        self.assertGreater(c.state["total_tokens"], 20_000_000)
        self.assertIsNone(c.store.brief().limits.max_total_tokens)
        self.assertIsNone(c.store.run().plan.max_iterations)

    async def test_report_is_barrier_and_waiting_never_requests_tokens(self) -> None:
        c = self.coordinator()
        branch = c.spawn(
            key="branch",
            direction="Reliability",
            instruction="Inspect runner",
            completion_criteria="Report actual interface",
        )
        researcher = branch["agent_id"]

        async def respond(agent_id: str, index: int, payload: Json) -> Json:
            return packet(call("wait" if agent_id == "rsi" else "report", f"{agent_id}_{index}"))

        transport = RoutedTransport(respond)
        engine = ResearchEngine(
            c, ResponsesRuntime(self.config, transport), lambda agent: self.tools(c, agent)
        )
        await engine.run_until_idle(timeout=3)
        self.assertEqual(c.actor(researcher).wait_reason, "rsi_reply")
        first_session = c.actor(researcher).session_id
        count = len(transport.payloads)
        for _ in range(3):
            await engine.tick()
            await asyncio.sleep(0)
        self.assertEqual(len(transport.payloads), count)
        self.assertEqual(engine.active_agents, ())
        report = c.store.records("reports", ResearchReport)[0]
        c.assign(
            researcher,
            key="next",
            instruction="Check cancellation",
            completion_criteria="Report cancellation behavior",
            reply_to_report_id=report.report_id,
        )
        await engine.run_until_idle(timeout=3)
        self.assertEqual(len(c.actor(researcher).iterations), 2)
        self.assertEqual(c.actor(researcher).session_id, first_session)
        self.assertEqual(transport.counts[researcher], 2)
        self.assertEqual(c.state["model_calls"], len(transport.payloads))
        self.assertEqual(c.state["total_tokens"], 30 * len(transport.payloads))

    async def test_researchers_are_parallel_while_rsi_does_its_own_research(self) -> None:
        c = self.coordinator()
        ids = [
            c.spawn(key=key, direction=key, instruction="Inspect", completion_criteria="Report")[
                "agent_id"
            ]
            for key in ("a", "b")
        ]
        started: set[str] = set()
        both_started, leader_researched, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def respond(agent_id: str, index: int, payload: Json) -> Json:
            if agent_id != "rsi":
                started.add(agent_id)
                if len(started) == 2:
                    both_started.set()
                await release.wait()
            return packet(
                call(
                    "search"
                    if agent_id == "rsi" and index == 0
                    else ("wait" if agent_id == "rsi" else "report"),
                    f"{agent_id}_{index}",
                )
            )

        def toolset(agent_id: str) -> ToolSet:
            usual = self.tools(c, agent_id)

            async def handle(item: ToolCall) -> ToolResult:
                if item.name == "search":
                    await both_started.wait()
                    self.assertTrue(set(ids).issubset(engine.active_agents))
                    leader_researched.set()
                    return ToolResult({"paper": "Evidence from independent leader research"})
                return await usual.call(item)

            return ToolSet([*usual.definitions, *definitions("search")], handle)

        transport = RoutedTransport(respond)
        engine = ResearchEngine(c, ResponsesRuntime(self.config, transport), toolset)
        task = asyncio.create_task(engine.run_until_idle(timeout=3))
        await asyncio.wait_for(leader_researched.wait(), 2)
        self.assertEqual(started, set(ids))
        self.assertFalse(release.is_set())
        release.set()
        await task
        self.assertEqual(len(c.store.records("reports", ResearchReport)), 2)
        self.assertEqual(c.actor("rsi").state, AgentState.WAITING)

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
            c, ResponsesRuntime(self.config, transport), lambda agent: self.tools(c, agent)
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
        self.assertEqual(
            len([item for item in transport.payloads[-1]["input"] if item.get("role") == "user"]), 1
        )
        self.assertIn(message_id, c.state["delivered"])
        self.assertTrue(
            any(record["reserved_tokens"] > 0 for record in c.state["model_requests"].values())
        )

    async def test_stop_cancels_active_work_without_resurrection(self) -> None:
        c = self.coordinator()
        started = asyncio.Event()

        async def respond(agent_id: str, index: int, payload: Json) -> Json:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        transport = RoutedTransport(respond)
        engine = ResearchEngine(
            c, ResponsesRuntime(self.config, transport), lambda agent: self.tools(c, agent)
        )
        await engine.tick()
        await asyncio.wait_for(started.wait(), 2)
        c.stop("User stopped")
        await engine.tick()
        self.assertEqual(engine.active_agents, ())
        self.assertEqual(c.actor("rsi").state, AgentState.STOPPED)
        await engine.run_until_idle(timeout=2)
        self.assertEqual(transport.counts["rsi"], 1)

    async def test_live_lock_prevents_second_owner_but_stale_file_does_not(self) -> None:
        c = self.coordinator(start=False)

        async def respond(agent_id: str, index: int, payload: Json) -> Json:
            raise AssertionError("No pending actor")

        runtime = ResponsesRuntime(self.config, RoutedTransport(respond))
        one = ResearchEngine(c, runtime, lambda agent: self.tools(c, agent))
        reopened = Coordinator(ResearchStore(self.root, c.store.run_id))
        two = ResearchEngine(reopened, runtime, lambda agent: self.tools(reopened, agent))
        await one.tick()
        with self.assertRaisesRegex(ResponsesError, "live worker"):
            await two.tick()
        await one.close()
        self.assertTrue(c.store.paths.execution_lock.exists())
        await two.tick()
        await two.close()

    async def test_crash_after_delivery_claim_but_before_session_creation(self) -> None:
        c = self.coordinator()
        message, resumed = c.begin_turn("rsi")
        self.assertFalse(resumed)
        reopened = Coordinator(ResearchStore(self.root, c.store.run_id))

        async def respond(agent_id: str, index: int, payload: Json) -> Json:
            return packet(call("wait", "wait"))

        transport = RoutedTransport(respond)
        engine = ResearchEngine(
            reopened,
            ResponsesRuntime(self.config, transport),
            lambda agent: self.tools(reopened, agent),
        )
        await engine.run_until_idle(timeout=3)
        self.assertEqual(transport.counts["rsi"], 1)
        self.assertIn(message.message_id, reopened.state["delivered"])

    async def test_usage_saved_before_worker_crash_is_reconciled_without_duplicate_api(
        self,
    ) -> None:
        c = self.coordinator()
        crashed = asyncio.Event()

        class InterruptedEngine(ResearchEngine):
            async def _event(self, agent_id: str, session: Path, event: Json) -> None:
                if event["type"] == "response_usage":
                    crashed.set()
                    raise asyncio.CancelledError
                await super()._event(agent_id, session, event)

        async def respond(agent_id: str, index: int, payload: Json) -> Json:
            return packet(call("wait", "wait"))

        transport = RoutedTransport(respond)
        engine = InterruptedEngine(
            c, ResponsesRuntime(self.config, transport), lambda agent: self.tools(c, agent)
        )
        await engine.tick()
        await asyncio.wait_for(crashed.wait(), 2)
        await engine.close()
        self.assertEqual(c.state["total_tokens"], 0)
        reopened = Coordinator(ResearchStore(self.root, c.store.run_id))
        resumed = ResearchEngine(
            reopened,
            ResponsesRuntime(self.config, transport),
            lambda agent: self.tools(reopened, agent),
        )
        await resumed.run_until_idle(timeout=3)
        self.assertEqual(reopened.state["total_tokens"], 30)
        self.assertEqual(transport.counts["rsi"], 1)

    async def test_global_model_budget_stops_before_next_request(self) -> None:
        c = self.coordinator(ResearchLimits(max_model_calls=1))

        async def respond(agent_id: str, index: int, payload: Json) -> Json:
            return packet(call("read", f"read_{index}"))

        async def read(_: ToolCall) -> ToolResult:
            return ToolResult({"observation": "Continue research"})

        transport = RoutedTransport(respond)
        engine = ResearchEngine(
            c,
            ResponsesRuntime(self.config, transport),
            lambda _: ToolSet(definitions("read"), read),
        )
        await engine.run_until_idle(timeout=3)
        self.assertEqual(c.state["status"], "paused")
        self.assertEqual(len(transport.payloads), 1)
        self.assertEqual(c.state["model_calls"], 1)

    async def test_mid_turn_compact_consumes_global_budget_without_resetting_turn(self) -> None:
        c = self.coordinator(ResearchLimits(max_model_calls=2))

        async def respond(agent_id: str, index: int, payload: Json) -> Json:
            if index == 0:
                return packet(call("read", "read_large"))
            self.assertNotIn("tools", payload)
            return packet(
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "Current evidence and next action"}
                    ],
                }
            )

        async def handler(_: ToolCall) -> ToolResult:
            return ToolResult("Long observed source text " * 1000)

        transport = RoutedTransport(respond)
        engine = ResearchEngine(
            c,
            ResponsesRuntime(replace(self.config, compact_after_chars=1000), transport),
            lambda _: ToolSet(definitions("read"), handler),
        )
        await engine.run_until_idle(timeout=3)
        self.assertEqual(c.state["status"], "paused")
        self.assertEqual(c.state["model_calls"], 2)
        self.assertEqual(c.state["total_tokens"], 60)
        self.assertEqual(len(transport.payloads), 2)
        session = c.store.paths.agent("rsi") / "sessions" / (c.actor("rsi").session_id or "rsi")
        saved = json.loads((session / "session.json").read_text())
        self.assertEqual(len(saved["compactions"]), 1)
        self.assertEqual(len(saved["turns"]), 1)
        self.assertEqual(saved["turns"][0]["requests"][1]["kind"], "compact")
        await engine.close()

    async def test_long_turn_checkpoint_uses_updated_plan_directions_and_budget(self) -> None:
        c = self.coordinator()
        branches: list[str] = []

        async def respond(agent_id: str, index: int, payload: Json) -> Json:
            self.assertEqual(agent_id, "rsi")
            if index != 1:
                self.assertIn("本次模型回合开始时的状态快照", payload["instructions"])
            if index == 0:
                return packet(call("plan", "make_plan"))
            if index == 1:
                self.assertNotIn("tools", payload)
                latest = json.loads(payload["input"][0]["content"])["authoritative_state"]
                self.assertEqual(latest["plan"], "Compare the memory layout direction")
                self.assertEqual(latest["directions"][0]["direction"], "Memory layout")
                self.assertEqual(latest["budget"]["model_calls"], 1)
                self.assertEqual(latest["budget"]["total_tokens"], 30)
                return packet(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": "The new layout direction is ready"}
                        ],
                    }
                )
            self.assertEqual(index, 2)
            return packet(call("advance", "after_checkpoint"))

        async def handle(item: ToolCall) -> ToolResult:
            if item.name == "plan":
                c.state["plan"] = "Compare the memory layout direction"
                c.save()
                branches.append(
                    c.spawn(
                        key="memory",
                        direction="Memory layout",
                        instruction="Inspect strides",
                        completion_criteria="Report concrete evidence",
                    )["agent_id"]
                )
                return ToolResult("Evidence from the plan update " * 150)
            c.state["plan"] = "A newer plan after the checkpoint"
            c.stop_agent(branches[0], "No further work is needed in this test")
            c.wait("rsi", "Await the next message")
            return ToolResult("waiting", stop=True)

        transport = RoutedTransport(respond)
        engine = ResearchEngine(
            c,
            ResponsesRuntime(replace(self.config, compact_after_chars=1000), transport),
            lambda _: ToolSet(definitions("plan", "advance"), handle),
            max_concurrent_turns=1,
        )
        await engine.run_until_idle(timeout=3)
        session = c.store.paths.agent("rsi") / "sessions" / (c.actor("rsi").session_id or "rsi")
        checkpoint = json.loads((session / "session.json").read_text())["compactions"][0]
        latest = checkpoint["authoritative_state"]
        self.assertEqual(latest["plan"], "Compare the memory layout direction")
        self.assertEqual(latest["directions"][0]["state"], "waiting")
        self.assertEqual(latest["budget"]["model_calls"], 2)
        self.assertEqual(latest["budget"]["total_tokens"], 60)
        self.assertEqual(c.state["plan"], "A newer plan after the checkpoint")
        self.assertEqual(c.state["model_calls"], 3)
        self.assertEqual(c.actor(branches[0]).state, AgentState.STOPPED)
        await engine.close()

    async def test_token_budget_reserves_input_and_output_before_call(self) -> None:
        c = self.coordinator(ResearchLimits(max_total_tokens=100))

        async def respond(agent_id: str, index: int, payload: Json) -> Json:
            raise AssertionError("Budget must refuse this request")

        transport = RoutedTransport(respond)
        engine = ResearchEngine(
            c, ResponsesRuntime(self.config, transport), lambda agent: self.tools(c, agent)
        )
        await engine.run_until_idle(timeout=3)
        self.assertEqual(c.state["status"], "paused")
        self.assertEqual(c.state["model_calls"], 0)
        self.assertEqual(transport.payloads, [])

    async def test_wall_budget_stops_idle_run_without_calling_model(self) -> None:
        c = self.coordinator(ResearchLimits(max_wall_seconds=1))
        started = datetime.fromisoformat(c.state["started_at"].replace("Z", "+00:00")).timestamp()

        async def respond(agent_id: str, index: int, payload: Json) -> Json:
            raise AssertionError("Time budget exhausted")

        engine = ResearchEngine(
            c,
            ResponsesRuntime(self.config, RoutedTransport(respond)),
            lambda agent: self.tools(c, agent),
            clock=lambda: started + 2,
        )
        await engine.run_until_idle(timeout=2)
        self.assertEqual(c.state["status"], "paused")
        self.assertEqual(c.state["model_calls"], 0)

    async def test_planned_interaction_can_respond_without_starting_rsi(self) -> None:
        c = self.coordinator(start=False)
        c.enqueue("user", "interaction", "Only change skills", key="user_1")

        async def respond(agent_id: str, index: int, payload: Json) -> Json:
            self.assertEqual(agent_id, "interaction")
            return packet(call("wait", "wait"))

        transport = RoutedTransport(respond)
        engine = ResearchEngine(
            c, ResponsesRuntime(self.config, transport), lambda agent: self.tools(c, agent)
        )
        await engine.run_until_idle(timeout=3)
        self.assertEqual(c.state["status"], "planned")
        self.assertEqual(c.state["model_calls"], 1)
        self.assertNotIn("rsi", transport.counts)

    async def test_waiting_job_hook_resumes_same_assignment_without_model_polling(self) -> None:
        c = self.coordinator()
        branch = c.spawn(
            key="job",
            direction="Runner",
            instruction="Run one job",
            completion_criteria="Report result",
        )
        researcher = branch["agent_id"]
        waiting, delivered = False, False
        polls = 0

        async def respond(agent_id: str, index: int, payload: Json) -> Json:
            name = "wait" if agent_id == "rsi" else ("wait_job" if index == 0 else "report")
            return packet(call(name, f"{agent_id}_{index}"))

        transport = RoutedTransport(respond)

        def toolset(agent_id: str) -> ToolSet:
            usual = self.tools(c, agent_id)

            async def handle(item: ToolCall) -> ToolResult:
                nonlocal waiting
                if item.name == "wait_job":
                    waiting = True
                    c.wait(agent_id, "job:one")
                    return ToolResult("Waiting for job one", stop=True, reason="waiting_for_job")
                return await usual.call(item)

            return ToolSet([*usual.definitions, *definitions("wait_job")], handle)

        async def poll_jobs() -> bool:
            nonlocal polls, delivered
            if not waiting or delivered:
                return False
            polls += 1
            self.assertEqual(transport.counts[researcher], 1)
            if polls == 3:
                c.enqueue("runner", researcher, "Job one completed; inspect result", key="job_done")
                delivered = True
            return not delivered

        engine = ResearchEngine(
            c, ResponsesRuntime(self.config, transport), toolset, tick_hook=poll_jobs
        )
        await engine.run_until_idle(timeout=3)
        self.assertEqual(polls, 3)
        self.assertEqual(transport.counts[researcher], 2)
        self.assertEqual(len(c.actor(researcher).iterations), 1)
        self.assertEqual(len(c.store.records("reports", ResearchReport)), 1)

    async def test_per_turn_budget_requires_explicit_recovery_not_automatic_retry(self) -> None:
        c = self.coordinator(ResearchLimits(max_model_calls=2))
        effects: list[str] = []

        async def respond(agent_id: str, index: int, payload: Json) -> Json:
            return packet(call("read", f"read_{index}"))

        async def read(item: ToolCall) -> ToolResult:
            effects.append(item.call_id)
            return ToolResult("Read one record")

        config = ResponsesConfig(
            base_url="https://example.test", api_key="", max_model_calls_per_turn=1
        )
        transport = RoutedTransport(respond)
        engine = ResearchEngine(
            c, ResponsesRuntime(config, transport), lambda _: ToolSet(definitions("read"), read)
        )
        await engine.run_until_idle(timeout=3)
        self.assertEqual(c.actor("rsi").state, AgentState.FAILED)
        self.assertIn("rsi", c.state["active"])
        await engine.run_until_idle(timeout=2)
        self.assertEqual(len(transport.payloads), 1)
        original_message = c.state["active"]["rsi"]
        c.retry_agent("rsi")
        retry_id = c.state["turn_retries"]["rsi"]["retry_id"]
        await engine.run_until_idle(timeout=3)
        self.assertEqual(len(transport.payloads), 2)
        self.assertEqual(c.actor("rsi").state, AgentState.FAILED)
        self.assertEqual(c.state["active"]["rsi"], original_message)
        self.assertEqual(c.state["model_calls"], 2)
        self.assertEqual(effects, ["read_0", "read_1"])
        # Explicit retry provides a local allowance, never more global budget.
        c.retry_agent("rsi")
        self.assertNotEqual(c.state["turn_retries"]["rsi"]["retry_id"], retry_id)
        await engine.run_until_idle(timeout=3)
        self.assertEqual(c.state["status"], "paused")
        self.assertEqual(len(transport.payloads), 2)
        self.assertEqual(c.state["model_calls"], 2)

    async def test_explicit_retry_continues_same_session_then_delivers_wait(self) -> None:
        c = self.coordinator()

        async def respond(agent_id: str, index: int, payload: Json) -> Json:
            return packet(call("read" if index == 0 else "wait", f"call_{index}"))

        effects: list[str] = []

        async def handle(item: ToolCall) -> ToolResult:
            effects.append(item.name)
            if item.name == "wait":
                c.wait("rsi", "Wait for the next input")
                return ToolResult("waiting", stop=True)
            return ToolResult("Read one record")

        config = ResponsesConfig(
            base_url="https://example.test", api_key="", max_model_calls_per_turn=1
        )
        transport = RoutedTransport(respond)

        def tools(_: str) -> ToolSet:
            return ToolSet(definitions("read", "wait"), handle)

        engine = ResearchEngine(c, ResponsesRuntime(config, transport), tools)
        await engine.run_until_idle(timeout=3)
        message_id = c.state["active"]["rsi"]
        c.retry_agent("rsi")
        await engine.close()
        # Both state machine and runtime are re-created after durable user retry.
        recovered = Coordinator(c.store)
        c = recovered
        second = ResearchEngine(recovered, ResponsesRuntime(config, transport), tools)
        await second.run_until_idle(timeout=3)
        self.assertEqual(recovered.actor("rsi").state, AgentState.WAITING)
        self.assertEqual(effects, ["read", "wait"])
        self.assertIn(message_id, recovered.state["delivered"])
        self.assertNotIn("rsi", recovered.state["turn_retries"])
        self.assertEqual(recovered.state["model_calls"], 2)
        actor_session = recovered.actor("rsi").session_id or "rsi"
        session = recovered.store.paths.agent("rsi") / "sessions" / actor_session / "session.json"
        saved = json.loads(session.read_text())
        self.assertEqual(len(saved["turns"]), 1)
        self.assertEqual(len(saved["turns"][0]["requests"]), 2)
        await second.close()

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
        engine = ResearchEngine(c, ResponsesRuntime(self.config, transport), tools)
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

    async def test_empty_final_reply_can_recover_without_replaying_completed_turn(self) -> None:
        c = self.coordinator()
        ra = c.spawn(key="a", direction="Inspect", instruction="Inspect the environment")[
            "agent_id"
        ]

        async def respond(agent_id: str, index: int, payload: Json) -> Json:
            if agent_id == "rsi":
                return packet(call("wait", f"wait_{index}"))
            if index == 0:
                return packet()
            return packet(
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "The environment is ready."}],
                }
            )

        transport = RoutedTransport(respond)
        engine = ResearchEngine(
            c, ResponsesRuntime(self.config, transport), lambda aid: self.tools(c, aid)
        )
        await engine.run_until_idle(timeout=3)
        session_id = c.actor(ra).session_id
        self.assertEqual(c.state["turn_failures"][ra]["kind"], "missing_report")
        c.retry_agent(ra)
        await engine.run_until_idle(timeout=3)
        self.assertEqual(c.actor(ra).session_id, session_id)
        self.assertEqual(len(c.actor(ra).iterations), 1)
        self.assertEqual(c.actor(ra).wait_reason, "rsi_reply")
        self.assertEqual(transport.counts[ra], 2)
        self.assertEqual(len(c.store.records("reports", ResearchReport)), 1)

    async def test_transport_failure_retry_resumes_original_turn_without_corrective_message(
        self,
    ) -> None:
        c = self.coordinator()
        c.begin_turn("rsi")
        c.wait("rsi", "Await the research report")
        c.end_turn("rsi")
        researcher = c.spawn(
            key="a", direction="A", instruction="Inspect", completion_criteria="Report"
        )["agent_id"]
        effects: list[str] = []

        async def respond(agent_id: str, index: int, payload: Json) -> Json:
            if agent_id == "rsi":
                return packet(call("wait", "rsi_wait"))
            if index == 0:
                return packet(call("read", "read_once"))
            if index == 1:
                raise ResponsesError("Temporary provider failure")
            self.assertEqual(index, 2)
            users = [item for item in payload["input"] if item.get("role") == "user"]
            self.assertEqual(len(users), 1)
            return packet(call("report", "report_after_retry"))

        def tools(agent_id: str) -> ToolSet:
            async def handle(item: ToolCall) -> ToolResult:
                if item.name == "read":
                    effects.append(item.call_id)
                    return ToolResult("Existing evidence")
                if item.name == "report":
                    return ToolResult(
                        c.report(agent_id, key=item.call_id, fields=fields()), stop=True
                    )
                c.wait(agent_id, "Await a relevant message")
                return ToolResult("waiting", stop=True)

            return ToolSet(definitions("read", "report", "wait"), handle)

        transport = RoutedTransport(respond)
        engine = ResearchEngine(c, ResponsesRuntime(self.config, transport), tools)
        await engine.run_until_idle(timeout=3)
        self.assertEqual(c.actor(researcher).state, AgentState.FAILED)
        self.assertNotIn(researcher, c.state.get("turn_failures", {}))
        original = c.state["active"][researcher]
        c.retry_agent(researcher)
        self.assertEqual(c.state["active"][researcher], original)
        await engine.run_until_idle(timeout=3)
        session = (
            c.store.paths.agent(researcher)
            / "sessions"
            / (c.actor(researcher).session_id or researcher)
        )
        saved = json.loads((session / "session.json").read_text())
        self.assertEqual(len(saved["turns"]), 1)
        self.assertEqual(saved["turns"][0]["message_id"], original)
        self.assertEqual(effects, ["read_once"])
        self.assertEqual(len(c.actor(researcher).iterations), 1)
        await engine.close()

    async def test_parallel_requests_cannot_all_reserve_the_same_remaining_budget(self) -> None:
        c = self.coordinator(ResearchLimits(max_agents=2, max_total_tokens=30000))
        for key in ("a", "b"):
            c.spawn(key=key, direction=key, instruction="Inspect", completion_criteria="Report")

        async def respond(agent_id: str, index: int, payload: Json) -> Json:
            await asyncio.Event().wait()
            raise AssertionError("Cancelled by the budget pause")

        transport = RoutedTransport(respond)
        engine = ResearchEngine(
            c, ResponsesRuntime(self.config, transport), lambda agent: self.tools(c, agent)
        )
        await engine.run_until_idle(timeout=3)
        self.assertEqual(c.state["status"], "paused")
        self.assertEqual(len(transport.payloads), 1)
        self.assertLessEqual(
            sum(item["reserved_tokens"] for item in c.state["model_requests"].values()), 30000
        )

    async def test_usage_attribution_stays_archived_without_inflating_live_budget(self) -> None:
        c = self.coordinator()
        attribution = {"items": {"large-provider-detail": {"input_tokens": 200}}}

        async def respond(agent_id: str, index: int, payload: Json) -> Json:
            if index == 0:
                return {
                    "status": "failed",
                    "error": {"code": "server_error"},
                    "output": [],
                    "usage": {"total_tokens": 3, "attribution": attribution},
                }
            response = packet(call("wait", "done"))
            response["usage"].update(
                attribution=attribution, input_tokens_details={"cached_tokens": 12}
            )
            return response

        config = replace(self.config, retry_initial_seconds=0.001, retry_max_seconds=0.002)
        runtime = ResponsesRuntime(config, RoutedTransport(respond))
        engine = ResearchEngine(c, runtime, lambda agent: self.tools(c, agent))
        await engine.run_until_idle(timeout=3)
        self.assertEqual(c.state["total_tokens"], 33)
        record = next(iter(c.state["model_requests"].values()))
        self.assertEqual(record["usage"]["input_tokens_details"], {"cached_tokens": 12})
        self.assertEqual(record["retry_usage"], {"1": {"total_tokens": 3}})
        self.assertNotIn("attribution", record["usage"])
        path = c.store.root / record["response_path"]
        archived = [path, *path.parent.glob(path.stem + ".attempt-*.json")]
        before = {item: item.read_bytes() for item in archived}
        self.assertEqual(len(archived), 2)
        for item in archived:
            self.assertEqual(json.loads(item.read_text())["usage"]["attribution"], attribution)
        await engine.close()
        restored = ResearchEngine(Coordinator(c.store), runtime, lambda a: self.tools(c, a))
        restored._acquire()
        self.assertEqual(restored.coordinator.state["total_tokens"], 33)
        self.assertEqual({item: item.read_bytes() for item in archived}, before)
        await restored.close()

    async def test_legacy_usage_migration_preserves_accounting_and_recovers_pending_response(
        self,
    ) -> None:
        c = self.coordinator()
        folder = c.store.root / "agents/rsi/sessions/rsi/responses"
        folder.mkdir(parents=True)
        usage = {"total_tokens": 30, "attribution": {"items": {"detail": [1, 2, 3]}}}
        saved = folder / "pending.json"
        saved.write_text(json.dumps({"status": "completed", "usage": usage}))
        before = saved.read_bytes()
        (folder / "old.json").write_text(json.dumps({"usage": {**usage, "total_tokens": 100}}))
        (folder / "old.attempt-1.json").write_text(
            json.dumps({"usage": {**usage, "total_tokens": 2}})
        )
        c.state["total_tokens"] = 102
        c.state["model_requests"] = {
            "old": {
                "settled": True,
                "usage": {**usage, "total_tokens": 100},
                "retry_usage": {"1": {**usage, "total_tokens": 2}},
                "reserved_tokens": 7,
                "unresolved_tokens": 7,
                "response_path": str((folder / "old.json").relative_to(c.store.root)),
            },
            "pending": {
                "settled": False,
                "reserved_tokens": 80,
                "unresolved_tokens": 5,
                "response_path": str(saved.relative_to(c.store.root)),
            },
            "missing_archive": {
                "settled": True,
                "usage": {**usage, "total_tokens": 0},
                "reserved_tokens": 0,
                "response_path": str((folder / "missing.json").relative_to(c.store.root)),
            },
        }
        c.save()

        async def respond(agent_id: str, index: int, payload: Json) -> Json:
            raise AssertionError("Recover accounting without making model calls")

        engine = ResearchEngine(
            c, ResponsesRuntime(self.config, RoutedTransport(respond)), lambda a: self.tools(c, a)
        )
        engine._acquire()
        records = c.state["model_requests"]
        self.assertEqual(records["old"]["usage"], {"total_tokens": 100})
        self.assertEqual(records["old"]["retry_usage"], {"1": {"total_tokens": 2}})
        self.assertEqual(records["old"]["reserved_tokens"], 7)
        self.assertEqual(records["pending"]["usage"], {"total_tokens": 30})
        self.assertEqual(records["pending"]["reserved_tokens"], 5)
        self.assertEqual(records["missing_archive"]["usage"], {**usage, "total_tokens": 0})
        self.assertEqual(c.state["total_tokens"], 132)
        engine._reconcile_usage()
        self.assertEqual(c.state["total_tokens"], 132)
        self.assertEqual(saved.read_bytes(), before)
        self.assertEqual(json.loads(c.path.read_text())["model_requests"], records)
        await engine.close()

    async def test_all_roles_retry_overload_and_keep_usage_without_failure_messages(self) -> None:
        from areal_rsi.ui.workbench import public_detail

        c = self.coordinator()
        researcher = c.spawn(
            key="retry-branch",
            direction="Reliability",
            instruction="Inspect",
            completion_criteria="Report",
        )["agent_id"]
        c.enqueue("user", "interaction", "Describe progress", key="user-progress")
        roles = {"rsi", "interaction", researcher}

        async def respond(agent_id: str, index: int, payload: Json) -> Json:
            if index < 5:
                return {
                    "status": "failed",
                    "error": {"code": "server_error"},
                    "output": [],
                    "usage": {"total_tokens": 2},
                }
            return packet(
                call("report" if agent_id == researcher else "wait", f"{agent_id}_{index}")
            )

        config = replace(self.config, retry_initial_seconds=0.001, retry_max_seconds=0.002)
        transport = RoutedTransport(respond)
        engine = ResearchEngine(c, ResponsesRuntime(config, transport), lambda a: self.tools(c, a))
        await engine.run_until_idle(timeout=5)
        self.assertTrue(all(c.actor(a).state == AgentState.WAITING for a in roles))
        self.assertTrue(all(transport.counts[a] >= 6 for a in roles))
        self.assertFalse(any(event.kind == "agent_failed" for event in c.store.events()))
        retries = [event for event in c.store.events() if event.kind == "model_retry"]
        self.assertEqual(len(retries), 15)
        self.assertEqual(c.state["total_tokens"], c.state["model_calls"] * 30 + 30)
        self.assertEqual(sum(r["reserved_tokens"] for r in c.state["model_requests"].values()), 0)
        self.assertGreater(c.summary()["budget"]["api_attempts"], c.state["model_calls"])
        # Restart reconciles saved retry usage without counting it twice.
        tokens = c.state["total_tokens"]
        restored = ResearchEngine(
            Coordinator(c.store), ResponsesRuntime(config, transport), lambda a: self.tools(c, a)
        )
        await restored.tick()
        self.assertEqual(restored.coordinator.state["total_tokens"], tokens)
        await restored.close()
        public = public_detail(
            {
                "agent_activity": {
                    "rsi": {
                        "type": "model_retry",
                        "attempt": 5,
                        "error_code": "server_error",
                        "next_retry_at": 1234,
                        "private_payload": "secret",
                    }
                }
            }
        )
        self.assertEqual(public["agent_activity"]["rsi"]["next_retry_at"], 1234)
        self.assertNotIn("secret", json.dumps(public))

    async def test_pause_during_infinite_retry_preserves_delivery(self) -> None:
        c = self.coordinator()

        async def respond(agent_id: str, index: int, payload: Json) -> Json:
            return {"status": "failed", "error": {"code": "server_error"}, "output": []}

        config = replace(self.config, retry_initial_seconds=60, retry_max_seconds=60)
        transport = RoutedTransport(respond)
        engine = ResearchEngine(c, ResponsesRuntime(config, transport), lambda a: self.tools(c, a))
        await engine.tick()
        for _ in range(100):
            if c.state.get("agent_activity", {}).get("rsi", {}).get("type") == "model_retry":
                break
            await asyncio.sleep(0.001)
        self.assertEqual(c.state["agent_activity"]["rsi"]["type"], "model_retry")
        active = c.state["active"]["rsi"]
        c.pause()
        await asyncio.wait_for(engine.tick(), 0.5)
        self.assertEqual(c.actor("rsi").state, AgentState.PAUSED)
        self.assertEqual(c.state["active"]["rsi"], active)
        self.assertEqual(transport.counts["rsi"], 1)
        await engine.close()


if __name__ == "__main__":
    unittest.main()


def test_busy_interaction_receives_user_steering_and_new_message_retries_failure(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        c = Coordinator.create(tmp_path, TaskBrief("研究", "运行时消息续接"))
        first = c.user_message("检查一下", key="first")
        seen: list[str] = []

        async def respond(agent: str, index: int, payload: Json) -> Json:
            if index == 0:
                c.user_message("直接开始", key="steering")
                return packet(call("read", "read"))
            seen.append(json.dumps(payload["input"], ensure_ascii=False))
            raise ResponsesError("Connection temporarily unavailable")

        async def handle(item: ToolCall) -> ToolResult:
            return ToolResult("ok")

        transport = RoutedTransport(respond)
        runtime = ResponsesRuntime(
            ResponsesConfig(
                base_url="https://example.test", api_key="test", max_model_calls_per_turn=1
            ),
            transport,
        )
        engine = ResearchEngine(c, runtime, lambda _: ToolSet(definitions("read"), handle))
        try:
            await engine._drive("interaction")
            assert "直接开始" in seen[0]  # Also crossed a one-call non-Interaction budget.
            assert c.actor("interaction").state == AgentState.FAILED
            from areal_rsi.api.research import ResearchMessage

            notices = [
                m for m in c.store.records("messages", ResearchMessage) if m.sender == "runtime"
            ]
            assert len(notices) == 1 and "已停止" in notices[0].text
            c.user_message("连接修好了，继续", key="resume")
            assert c.actor("interaction").state == AgentState.WAITING
            assert c.state["active"]["interaction"] == first
            retry = c.state["turn_retries"]["interaction"]["retry_id"]
            c.user_message("连接修好了，继续", key="resume")
            assert c.state["turn_retries"]["interaction"]["retry_id"] == retry
        finally:
            await engine.close()

    asyncio.run(scenario())


def test_supervisor_update_arriving_with_report_is_consumed_before_report(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Coordinator.create(tmp_path, TaskBrief("任务", "执行中的负责人补充"))
        c.start()
        branch = c.spawn(
            key="worker", direction="接入", instruction="实现接口", completion_criteria="报告"
        )
        researcher = branch["agent_id"]
        updates: list[str] = []

        async def respond(agent: str, index: int, payload: Json) -> Json:
            if index == 0:
                updates.append(
                    c.message_researcher(researcher, "有效分数不等于达到目标", key="update")
                )
            else:
                assert "有效分数不等于达到目标" in json.dumps(payload["input"], ensure_ascii=False)
                assert not c.pending_updates(researcher)
            return packet(call("report", "report" + str(index)))

        async def handle(item: ToolCall) -> ToolResult:
            return ToolResult(c.report(researcher, key=item.call_id, fields=fields()), stop=True)

        transport = RoutedTransport(respond)
        runtime = ResponsesRuntime(
            ResponsesConfig(base_url="https://example.test", api_key="test"), transport
        )
        engine = ResearchEngine(c, runtime, lambda _: ToolSet(definitions("report"), handle))
        try:
            await engine._drive(researcher)
            assert transport.counts[researcher] == 2
            actor = c.actor(researcher)
            assert len(actor.iterations) == 1 and actor.iterations[0].report_ref is not None
            assert updates[0] in c.state["delivered"]
            assert (
                c.message_researcher(researcher, "有效分数不等于达到目标", key="update")
                == updates[0]
            )
            try:
                c.message_researcher(researcher, "下一项任务", key="after_report")
            except ValueError as exc:
                assert "assign" in str(exc)
            else:
                raise AssertionError("Report barrier was bypassed")
        finally:
            await engine.close()

    asyncio.run(scenario())
