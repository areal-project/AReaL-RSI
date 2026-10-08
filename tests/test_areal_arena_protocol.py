"""Optional contract checks for the isolated AReaL Arena protocol patch."""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("AREAL_CHECKOUT"), reason="AReaL checkout not configured"
)


@pytest.mark.parametrize(
    ("protocol", "expected"),
    [("anthropic", ["chat", "messages"]), ("chat_completions", ["chat"])],
)
def test_arena_route_advertises_native_anthropic(protocol: Any, expected: list[str]) -> None:
    from examples.swe.arena_client import _llm_registration_payload

    payload = _llm_registration_payload(
        "stream-areal-test", "http://local-proxy:8000", "private-test-key", "test", protocol
    )
    assert payload["endpoints"][0]["inbound_protos"] == expected


@pytest.mark.parametrize(
    ("system", "expected"),
    [
        ("Solve accurately.", "Solve accurately."),
        (
            [
                {
                    "type": "text",
                    "text": "Solve accurately.",
                    "cache_control": {"type": "ephemeral"},
                },
                {"type": "text", "text": "Give an exact answer."},
            ],
            "Solve accurately.\nGive an exact answer.",
        ),
    ],
)
def test_anthropic_system_blocks_remain_text(system: Any, expected: str) -> None:
    from areal.experimental.openai.anthropic import translate_anthropic_request

    request = {
        "model": "qwen",
        "max_tokens": 64,
        "system": system,
        "messages": [{"role": "user", "content": "What is 2+3?"}],
    }
    translated = translate_anthropic_request(request)
    assert translated["messages"][0] == {"role": "system", "content": expected}
    assert request["system"] == system


def test_anthropic_unsupported_system_block_is_rejected() -> None:
    from areal.experimental.openai.anthropic import translate_anthropic_request

    with pytest.raises(ValueError, match="system blocks"):
        translate_anthropic_request(
            {
                "model": "qwen",
                "max_tokens": 64,
                "system": [{"type": "image", "source": {}}],
                "messages": [{"role": "user", "content": "What is 2+3?"}],
            }
        )


@pytest.mark.parametrize("failed", [False, True])
def test_v2_preserves_arena_audit_and_rejects_failed_episodes(
    monkeypatch: pytest.MonkeyPatch, failed: bool
) -> None:
    from areal.infra import workflow_context
    from areal.v2.inference_service.controller import workflow

    calls: list[tuple[str, float | None]] = []

    class Agent:
        async def run(self, data: Any, **kwargs: Any) -> float:
            assert kwargs["session_id"] == "session-one"
            if failed:
                raise RuntimeError("Arena infrastructure failed")
            return 1.0

        async def persist_episode_result(self, data: Any, reward: float) -> None:
            calls.append(("persist", reward))

        def record_episode_metrics(self, data: Any, reward: float) -> None:
            calls.append(("metrics", reward))

        async def record_failure_disposition(self, data: Any, error: Any, disposition: str) -> None:
            assert disposition == "unknown_failure_reject"
            calls.append(("rejected", None))

    runner = workflow.InferenceServiceWorkflow(
        SimpleNamespace(get_version=lambda: 0), Agent(), group_size=1
    )

    async def start(*args: Any, **kwargs: Any) -> Any:
        return "group-one", [("session-one", "private-test-key")]

    async def reward(*args: Any, **kwargs: Any) -> int:
        calls.append(("reward", args[1]))
        return 0

    async def export(*args: Any, **kwargs: Any) -> Any:
        return {} if kwargs["discard_trajectory"] else {"trajectory": "valid"}

    async def client() -> None:
        return None

    monkeypatch.setattr(runner, "_start_session", start)
    monkeypatch.setattr(runner, "_set_last_reward", reward)
    monkeypatch.setattr(runner, "_export_interactions", export)
    monkeypatch.setattr(workflow_context, "get", lambda: SimpleNamespace(task_id="local-task"))
    monkeypatch.setattr(workflow_context, "get_httpx_client", client)
    monkeypatch.setattr(workflow_context, "stat_scope", lambda: "test")
    monkeypatch.setattr(
        workflow.stats_tracker, "get", lambda _: SimpleNamespace(scalar=lambda **kw: None)
    )
    result = asyncio.run(runner._run_offline(None, {}))
    if failed:
        assert result is None and calls == [("rejected", None)]
    else:
        assert result == {"trajectory": "valid"}
        assert calls == [("reward", 1.0), ("persist", 1.0), ("metrics", 1.0)]
