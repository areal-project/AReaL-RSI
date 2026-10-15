from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from runtime_fakes import ActionRuntime

from areal_rsi.api.interfaces import ToolCall
from areal_rsi.api.research import TaskBrief
from areal_rsi.infra.config import ResearchConnection
from areal_rsi.infra.coordination.config import ResearchConfig, config_from_dict
from areal_rsi.infra.coordination.tools.actions import ResearchActions
from areal_rsi.infra.coordination.tools.brief import PASSK_HIDDEN_LIMITS
from areal_rsi.infra.execution import ApprovedCommand
from areal_rsi.infra.research import ResearchSystem
from areal_rsi.infra.runtime.research_runtime import ResearchRuntime
from areal_rsi.infra.storage.files import encode, write_json


def test_current_brief_can_be_updated_and_started(tmp_path: Path) -> None:
    async def scenario() -> None:
        system = ResearchSystem(tmp_path, runtime_factory=lambda _: ActionRuntime())
        try:
            run_id = await system.create(ResearchConfig(TaskBrief("new", "describe")))
            c = system.coordinator(run_id)
            arguments = encode(c.store.brief())
            for key in (*PASSK_HIDDEN_LIMITS, "max_agents", "max_effective_seconds"):
                arguments["limits"].pop(key, None)
            arguments.update(objective="Research", instructions="Keep fixed data",
                             acceptance=["Run the configured checks using fixed data"])
            result = await ResearchActions(c).handler("interaction")(
                ToolCall("update_brief", arguments, "update")
            )
            assert "error" not in result.output
            assert c.store.brief().instructions == "Keep fixed data"
            config = system.config(c)
            config.commands = (ApprovedCommand("noop", (sys.executable, "-c", "pass")),)
            write_json(c.store.root / "coordination/config.json", config.public())
            c.state.intake.status = "ready"
            c.save()
            await system.start(c.store.run_id)
            assert system.coordinator(c.store.run_id).state.status == "running"
            started = json.loads((c.store.root / "coordination/started-config.json").read_text())
            assert started["brief"]["limits"]["max_model_calls"] is None
        finally:
            await system.close()

    asyncio.run(scenario())


def test_unknown_config_is_an_operator_error() -> None:
    with pytest.raises(ValueError, match="Unknown research configuration fields: retrys"):
        config_from_dict({"brief": {"objective": "test", "instructions": ""}, "retrys": 2})


@pytest.mark.parametrize("path", ["", "/", "/v1", "/v1/", "/v1/responses"])
def test_connection_normalizes_the_native_provider_base(path: str) -> None:
    connection = ResearchConnection("https://example.test" + path, "fixture", "high")
    assert connection.runtime_base_url == "https://example.test/v1"


@pytest.mark.parametrize("url", [
    "file:///tmp/model", "https://user:secret@example.test", "https://example.test?key=secret",
    "https://example.test#fragment", "https://bad host.test", "http://example.test:0",
    "http://example.test:99999", "http://[::1",
])
def test_invalid_provider_urls_are_rejected(url: str) -> None:
    with pytest.raises(ValueError, match="HTTP"):
        ResearchConnection(url, "fixture", "high")


@pytest.mark.parametrize("tokens,window", [
    (0, None), (True, None), (1.5, None), (10, 10), (10, -1), (128000, None),
])
def test_invalid_token_settings_are_rejected(tokens: int, window: int | None) -> None:
    with pytest.raises(ValueError):
        ResearchConnection("https://example.test", "fixture", "high", tokens, window)


def test_unenforceable_request_cap_is_rejected() -> None:
    cap = 200
    with pytest.raises(ValueError, match="max_model_calls is not supported"):
        config_from_dict({"brief": {
            "objective": "test", "instructions": "test", "limits": {"max_model_calls": cap},
        }})


def test_codex_custom_output_limit_is_rejected() -> None:
    with pytest.raises(ValueError, match="only configurable for standard"):
        config_from_dict({
            "brief": {"objective": "test", "instructions": "test"},
            "backend": "codex", "max_output_tokens": 4096,
        })


def test_all_native_roles_use_one_pinned_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RSI_API_KEY", "fixture")
    monkeypatch.setenv("RSI_MODEL", "gpt-6-astra")
    monkeypatch.setenv("RSI_BACKEND", "codex")
    config = config_from_dict({"brief": {"objective": "test", "instructions": "test"}})
    runtime = ResearchRuntime(config)
    monkeypatch.setenv("RSI_MODEL", "another-model")
    monkeypatch.setenv("RSI_BACKEND", "standard")
    assert runtime.backend == "codex"
    assert runtime.configuration()["model"] == "gpt-6-astra"
    assert runtime.for_interaction().connection == runtime.connection
    assert runtime.for_interaction().backend == "codex"
    assert not config.model_settings_pinned, "Constructing a runtime must not mutate caller config"


@pytest.mark.parametrize("window", [None, 128_000])
def test_research_context_budget_survives_pinning_and_configuration_recovery(
    monkeypatch: pytest.MonkeyPatch, window: int | None
) -> None:
    monkeypatch.setenv("RSI_API_KEY", "test")
    monkeypatch.setenv("RSI_MODEL", "gpt-6-astra")
    config = config_from_dict({
        "brief": {"objective": "Research", "instructions": "Improve an artifact"},
        "model": "unknown-gateway-model", "context_window_tokens": window,
    })
    assert config.runtime().effective_context_window_tokens == (window or 256_000)
    config.pin_model_settings()
    recovered = config_from_dict(config.public())
    monkeypatch.setenv("RSI_MODEL", "another-model")
    assert recovered.runtime().model == "gpt-6-astra"
    assert recovered.runtime().effective_context_window_tokens == (window or 256_000)
    assert recovered.context_window_tokens == window


def test_runtime_profile_is_frozen_and_routes_roles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "runtime.json"
    profile = {
        "default": {"kind": "hermes", "config": {"hermesRoot": "/opt/hermes"}},
        "roles": {"interaction-agent": {"kind": "dsh", "config": {}}},
    }
    path.write_text(json.dumps(profile))
    monkeypatch.delenv("RSI_BACKEND", raising=False)
    monkeypatch.setenv("RSI_RUNTIME_CONFIG", str(path))
    monkeypatch.setenv("RSI_API_KEY", "fixture")
    config = config_from_dict({"brief": {"objective": "test", "instructions": "test"}})
    config.pin_model_settings()
    assert config.backend == "runtime"
    path.write_text('{"default":{"kind":"fake"}}')
    restored = config_from_dict(config.public())
    runtime = ResearchRuntime(restored)
    assert runtime.configuration_for("research-agent-1")["harness"] == "hermes"
    assert runtime.configuration_for("rsi")["harness"] == "hermes"
    assert runtime.configuration_for("interaction")["harness"] == "dsh"
    assert restored.runtime_config == profile


def test_runtime_profile_rejects_literal_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RSI_BACKEND", raising=False)
    config = config_from_dict({
        "brief": {"objective": "test", "instructions": "test"},
        "runtime_config": {"default": {"kind": "dsh", "config": {"apiKey": "secret"}}},
    })
    with pytest.raises(ValueError, match="literal Runtime secrets"):
        config.pin_model_settings()
