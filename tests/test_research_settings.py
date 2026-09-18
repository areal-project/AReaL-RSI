from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
from runtime_fakes import ActionRuntime

from areal_rsi.base.storage import encode
from areal_rsi.coordination.actions import ResearchActions
from areal_rsi.coordination.config import config_from_dict
from areal_rsi.modules.agent_runtime import ToolCall
from areal_rsi.modules.research_runtime import ResearchRuntime
from areal_rsi.modules.research_settings import ResearchConnection
from areal_rsi.research_app import ResearchApp

RETIRED = {
    "compact_keep_recent_tokens": 8000,
    "max_model_calls_per_turn": None,
    "retry_initial_seconds": 2,
    "retry_max_seconds": 60,
}


@pytest.mark.parametrize("field,value", RETIRED.items())
def test_removed_configuration_fields_are_rejected(field: str, value: int | None) -> None:
    with pytest.raises(ValueError, match="Unknown research configuration fields: " + field):
        config_from_dict({"brief": {"objective": "test", "instructions": "test"}, field: value})


def test_current_brief_can_be_updated_and_started(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = ResearchApp(tmp_path, runtime_factory=lambda _: ActionRuntime())
        try:
            detail = await app.create({"conversation": True})
            c = app.coordinator(detail["run_id"])
            arguments = encode(c.store.brief())
            arguments.update(objective="Research", instructions="Keep fixed data")
            result = await ResearchActions(c).handler("interaction")(
                ToolCall("update_brief", arguments, "update")
            )
            assert "error" not in result.output
            assert c.store.brief().instructions == "Keep fixed data"
            c.state["intake"]["status"] = "ready"
            c.state["execution_setup"] = "pending"
            c.save()
            await app.start(c)
            assert app.coordinator(c.store.run_id).state["status"] == "running"
            started = json.loads((c.store.root / "coordination/started-config.json").read_text())
            assert "max_model_calls" not in started["brief"]["limits"]
        finally:
            await app.close()

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


def test_research_imports_do_not_load_legacy_responses(tmp_path: Path) -> None:
    subprocess.run(
        [sys.executable, "-c", "import sys; import areal_rsi.research_app; "
         "import areal_rsi.ui.preflight; assert 'areal_rsi.modules.responses' not in sys.modules"],
        cwd=tmp_path, check=True, capture_output=True, text=True, timeout=10,
    )


@pytest.mark.parametrize("cap", [None, 200])
def test_removed_request_cap_is_rejected(cap: int | None) -> None:
    with pytest.raises(ValueError, match="Unknown research configuration field: max_model_calls"):
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
