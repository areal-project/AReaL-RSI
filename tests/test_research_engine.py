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
from areal_rsi.base.storage import write_json
from areal_rsi.coordination.actions import ResearchActions
from areal_rsi.coordination.agent_config import AgentConfig, AgentOverrides
from areal_rsi.coordination.config import ResearchConfig, load_config
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
    async def test_yaml_overrides_both_roles_and_reads_isolated_skills_after_recovery(self) -> None:
        c = self.coordinator()
        for role in ("rsi", "research"):
            folder = self.root / role
            (folder / "skills").mkdir(parents=True)
            (folder / "prompt.md").write_text(f"CUSTOM_{role}_PROMPT")
            (folder / "skills/SKILL.md").write_text(f"PRIVATE_{role}_SKILL")
        source = self.root / "experiment.yaml"
        source.write_text(
            "brief:\n  objective: Inspect\n  instructions: Report\nagents:\n"
            "  research:\n    prompt: research/prompt.md\n    skills: research/skills\n"
            "  rsi:\n    prompt: rsi/prompt.md\n    skills: rsi/skills\n"
        )
        config = load_config(source)
        write_json(c.store.root / "coordination/config.json", config.public())
        researcher = c.spawn(
            key="both",
            direction="Check",
            instruction="Inspect evidence",
            completion_criteria="Report findings",
        )["agent_id"]
        actions = ResearchActions(c)

        async def respond(agent_id: str, index: int, payload: Json) -> Json:
            role = "rsi" if agent_id == "rsi" else "research"
            other = "research" if role == "rsi" else "rsi"
            self.assertIn(f"CUSTOM_{role}_PROMPT", payload["instructions"])
            self.assertNotIn(f"CUSTOM_{other}_PROMPT", payload["instructions"])
            self.assertNotIn("你负责 RSI 指派的研究任务", payload["instructions"])
            if index == 0:
                return packet(
                    {
                        **call("read_agent_skill", f"{role}_skill"),
                        "arguments": '{"path":"SKILL.md"}',
                    }
                )
            history = json.dumps(payload["input"], ensure_ascii=False)
            self.assertIn(f"PRIVATE_{role}_SKILL", history)
            self.assertNotIn(f"PRIVATE_{other}_SKILL", history)
            if role == "rsi":
                return packet(
                    {
                        **call("wait", f"rsi_wait_{index}"),
                        "arguments": '{"reason":"Await findings"}',
                    }
                )
            return packet(
                {
                    "type": "message",
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [
                        {"type": "output_text", "text": "Checked evidence; ready for followup."}
                    ],
                }
            )

        def tools(agent_id: str) -> ToolSet:
            return ToolSet(actions.definitions(agent_id), actions.handler(agent_id))

        transport = RoutedTransport(respond)
        engine = ResearchEngine(c, ResponsesRuntime(self.config, transport), tools)
        await engine.run_until_idle(timeout=3)
        sessions = {a: c.actor(a).session_id for a in ("rsi", researcher)}
        for role in ("rsi", "research"):
            (self.root / role / "prompt.md").unlink()
            (self.root / role / "skills/SKILL.md").write_text("CHANGED SOURCE")
        c.assign(
            key="again",
            agent_id=researcher,
            instruction="Check again",
            completion_criteria="Report",
        )
        engine = ResearchEngine(c, ResponsesRuntime(self.config, transport), tools)
        await engine.run_until_idle(timeout=3)
        for agent_id, session_id in sessions.items():
            self.assertEqual(c.actor(agent_id).session_id, session_id)
            self.assertGreaterEqual(transport.counts[agent_id], 3)
        for agent_id, role in (("rsi", "rsi"), (researcher, "research")):
            result = await actions.handler(agent_id)(
                ToolCall("read_agent_skill", {"path": "SKILL.md"}, "after_recovery")
            )
            self.assertEqual(result.output["text"], f"PRIVATE_{role}_SKILL")

    async def test_custom_role_prompt_replaces_only_research_and_survives_followup(self) -> None:
        c = self.coordinator()
        source = self.root / "custom.md"
        source.write_text("UNIQUE CUSTOM RESEARCH ROLE")
        method = AgentConfig(prompt=str(source))
        method.freeze()
        config = ResearchConfig(c.store.brief(), agents=AgentOverrides(research=method))
        write_json(c.store.root / "coordination/config.json", config.public())
        researcher = c.spawn(
            key="custom",
            direction="Check",
            instruction="Inspect evidence",
            completion_criteria="Report findings",
        )["agent_id"]

        async def respond(agent_id: str, index: int, payload: Json) -> Json:
            prompt = payload["instructions"]
            if agent_id == researcher:
                self.assertIn("UNIQUE CUSTOM RESEARCH ROLE", prompt)
                self.assertNotIn("你负责 RSI 指派的研究任务", prompt)
                return packet(call("report", f"report_{index}"))
            self.assertNotIn("UNIQUE CUSTOM RESEARCH ROLE", prompt)
            return packet(call("wait", f"wait_{index}"))

        transport = RoutedTransport(respond)
        engine = ResearchEngine(
            c, ResponsesRuntime(self.config, transport), lambda a: self.tools(c, a)
        )
        await engine.run_until_idle(timeout=3)
        session_id = c.actor(researcher).session_id
        source.unlink()
        c.assign(
            key="followup",
            agent_id=researcher,
            instruction="Check more evidence",
            completion_criteria="Report",
        )
        # Reattach an engine as on recovery; source no longer exists.
        engine = ResearchEngine(
            c, ResponsesRuntime(self.config, transport), lambda a: self.tools(c, a)
        )
        await engine.run_until_idle(timeout=3)
        self.assertEqual(c.actor(researcher).session_id, session_id)
        self.assertEqual(transport.counts[researcher], 2)

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
