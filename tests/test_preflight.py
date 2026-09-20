"""Exercise the actual native backend against local scripted inference."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
from pathlib import Path
from typing import Any

import pytest
from test_research_runtime_package import ResponsesModel

from areal_rsi.api.research import TaskBrief
from areal_rsi.infra.coordination.config import ResearchConfig
from areal_rsi.ui.preflight import preflight


# The retry case also covers a successful restart; do not repeat it without an outage.
@pytest.mark.parametrize("backend,wrong_memory,outages", [
    ("codex", False, 2),
    ("codex", True, 0),
    pytest.param("standard", False, 0, marks=pytest.mark.skipif(
        os.environ.get("RSI_TEST_NATIVE_HARNESSES") != "1", reason="Opt in to native DSH"
    )),
])
def test_preflight_checks_native_tools_restart_and_duplicate_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str, wrong_memory: bool, outages: int
) -> None:
    monkeypatch.setenv("RSI_API_KEY", "fixture")
    model = ResponsesModel()
    model.fail_after_tool = outages

    def output(request: dict[str, Any]) -> dict[str, Any]:
        inputs = request["input"]
        user_index = max(i for i, item in enumerate(inputs) if item.get("role") == "user")
        if any(item.get("type") == "function_call_output" for item in inputs[user_index + 1:]):
            return {
                "id": "msg_fixture", "type": "message", "role": "assistant",
                "phase": "final_answer", "status": "completed",
                "content": [{"type": "output_text", "text": "Done", "annotations": []}],
            }
        context = json.dumps(inputs)
        match = re.search(r"Remember this value: ([a-f0-9]+)", context)
        assert match is not None, "Restart lost the first native turn's message"
        second = "Acknowledge the value I asked you" in json.dumps(inputs[user_index])
        value = ("wrong" if wrong_memory else match[1]) if second else "ready"
        identifier = "ack_second" if second else "ack_first"
        if backend == "codex":
            name = "exec_command"
            arguments = {"cmd": "rsi-tools call acknowledge "
                         + shlex.quote(json.dumps({"value": value})) + " " + identifier}
        else:
            name = next(t["name"] for t in request["tools"] if "acknowledge" in t["name"])
            arguments = {"value": value}
        return {
            "id": "fc_" + identifier, "type": "function_call", "call_id": identifier,
            "name": name, "arguments": json.dumps(arguments), "status": "completed",
        }

    model.output = output
    config = ResearchConfig(
        TaskBrief("preflight", "test"), backend=backend, base_url=model.url,
        model_settings_pinned=True,
    )
    try:
        if wrong_memory:
            with pytest.raises(ValueError, match="preserve its identity and memory"):
                asyncio.run(preflight(config, root=tmp_path, timeout=60))
        else:
            result = asyncio.run(preflight(config, root=tmp_path, timeout=60))
            assert result["backend"] == backend
            assert result["runtime"]["runtime"] == "areal-agent-runtime"
            assert result["tool_calls"] == 2
            assert result["restart_continuation"] is True
            assert result["duplicate_delivery"] == "zero turns"
            assert all(usage["total_tokens"] > 0 for usage in result["usage"])
            session = Path(result["records"])
            assert session.is_relative_to(tmp_path / "preflight")
            assert (session / "runtime/data/state/journal.jsonl").exists()
            assert len(list((session / "deliveries").glob("*.json"))) == 2
        assert len(model.requests) == 4 + outages, (
            "Retry and replay must not repeat committed tools"
        )
        assert model.fail_after_tool == 0
        if outages:
            # Native retries preserve the tool result already committed before the outage.
            assert all(
                request["input"] == model.requests[1]["input"]
                for request in model.requests[2:outages + 2]
            )
    finally:
        model.close()
