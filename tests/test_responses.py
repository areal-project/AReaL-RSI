"""Real Responses wire shapes with deterministic transports and crash recovery."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

from areal_rsi.modules.responses import (
    EventSink,
    ResponsesConfig,
    ResponsesRuntime,
    ToolCall,
    ToolResult,
)

Json = dict[str, Any]


def answer(text: str, *, phase: str = "final_answer") -> Json:
    return {
        "type": "message",
        "id": "msg_example",
        "role": "assistant",
        "status": "completed",
        "phase": phase,
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def call(name: str, identifier: str, arguments: str = "{}") -> Json:
    return {
        "type": "function_call",
        "id": "fc_" + identifier,
        "call_id": identifier,
        "name": name,
        "arguments": arguments,
        "status": "completed",
    }


def response(*output: Json, status: str = "completed") -> Json:
    return {
        "id": "resp_example",
        "object": "response",
        "status": status,
        "output": list(output),
        "usage": {
            "input_tokens": 20,
            "output_tokens": 5,
            "total_tokens": 25,
            "output_tokens_details": {"reasoning_tokens": 3},
        },
    }


class ScriptedTransport:
    def __init__(self, *responses: Json):
        self.responses = list(responses)
        self.payloads: list[Json] = []

    async def request(self, config: ResponsesConfig, payload: Json, event_sink: EventSink) -> Json:
        self.payloads.append(copy.deepcopy(payload))
        if not self.responses:
            raise AssertionError("Unexpected model request, including a possible waiting poll")
        return self.responses.pop(0)


async def reject_call(call: ToolCall) -> ToolResult:
    raise AssertionError("Unexpected tool call: " + call.name)


class ResponsesRuntimeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "session"
        self.config = ResponsesConfig(base_url="https://example.test", api_key="unit-test-secret")

    def saved(self) -> Json:
        value: Json = json.loads((self.root / "session.json").read_text())
        return value

    async def test_compact_preserves_archive_and_supplies_authoritative_recovery_state(
        self,
    ) -> None:
        transport = ScriptedTransport(
            response(
                {"type": "reasoning", "encrypted_content": "opaque-state-not-source-evidence"},
                answer("Long evidence " * 150),
            ),
            response(
                {**answer("Temporary summary drafting"), "phase": "commentary"},
                {
                    **answer("Objective: optimize. Evidence: record_12. Next: compare."),
                    "phase": "final_answer",
                },
            ),
            response(answer("Continued")),
        )
        runtime = ResponsesRuntime(replace(self.config, compact_after_chars=1000), transport)
        first = await runtime.turn(
            self.root, system_prompt="Research", user_message="Begin", tools=[], handler=reject_call
        )
        second = await runtime.turn(
            self.root,
            system_prompt="Research",
            user_message="Compare now",
            tools=[],
            handler=reject_call,
            recovery_state={"best_version": "v2", "active_jobs": ["j1"]},
        )
        self.assertEqual(first.session_id, second.session_id)
        state = self.saved()
        self.assertEqual(len(state["compactions"]), 1)
        self.assertIn("Long evidence " * 150, json.dumps(state["history"]))
        self.assertNotIn("Long evidence " * 150, json.dumps(transport.payloads[-1]["input"]))
        self.assertIn("v2", transport.payloads[-1]["input"][0]["content"])
        self.assertEqual(transport.payloads[-1]["input"][0]["role"], "assistant")
        self.assertEqual(transport.payloads[-1]["input"][-1]["content"], "Compare now")
        self.assertNotIn("tools", transport.payloads[1])
        compact_payload = transport.payloads[1]
        archive = json.loads(compact_payload["input"][0]["content"])
        self.assertEqual(len(compact_payload["input"]), 1)
        self.assertEqual(archive["conversation"][0]["content"], "Begin")
        self.assertNotIn("Research", compact_payload["instructions"])
        self.assertEqual(archive["authoritative_state"]["best_version"], "v2")
        self.assertNotIn("opaque-state-not-source-evidence", json.dumps(compact_payload))
        self.assertIn("opaque-state-not-source-evidence", json.dumps(state["history"]))
        self.assertNotIn("Temporary summary drafting", state["compactions"][0]["summary"])


if __name__ == "__main__":
    unittest.main()
