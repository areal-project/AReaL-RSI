"""Exercise scheduler decisions with real durable Responses turns and mock inference."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from areal_rsi.api.base import AgentState
from areal_rsi.api.research import ResearchLimits, ResearchReport, TaskBrief
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.coordination.engine import ResearchEngine, ToolSet
from areal_rsi.modules.responses import (
    EventSink,
    ResponsesConfig,
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


if __name__ == "__main__":
    unittest.main()


def test_wall_budget_can_transition_to_finalization_instead_of_pausing(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Coordinator.create(
            tmp_path, TaskBrief("finish on timeout", "", ResearchLimits(max_wall_seconds=1))
        )
        c.start()
        engine = ResearchEngine(
            c,
            ResponsesRuntime(ResponsesConfig(api_key="fixture", base_url="https://example.test")),
            lambda _: ToolSet([], None),  # type: ignore[arg-type]
            on_wall_time_exhausted=lambda: c.stop("finalize"),
        )
        engine._pause_for_budget("Run wall-time budget exhausted")
        assert c.state["status"] == "stopped"
        await engine.close()

    asyncio.run(scenario())
