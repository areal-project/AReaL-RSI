"""Exercise scheduler decisions with real durable Responses turns and mock inference."""

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
        raw = payload["instructions"].split("Current authoritative run state:\n", 1)[1]
        state = json.JSONDecoder().raw_decode(raw)[0]
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
        c = self.coordinator()

        async def respond(agent_id: str, index: int, payload: Json) -> Json:
            return packet(call("read", f"read_{index}"))

        async def read(_: ToolCall) -> ToolResult:
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


if __name__ == "__main__":
    unittest.main()
