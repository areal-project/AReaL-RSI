from __future__ import annotations

import json

import pytest

from areal_rsi.config import AgentModelSettings, ModelConfigurationError


@pytest.fixture
def defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RSI_TEACHER_MODEL", "teacher-default-model")
    monkeypatch.setenv("RSI_TEACHER_BASE_URL", "https://teacher.example/v1")
    monkeypatch.setenv("RSI_TEACHER_API_KEY", "teacher-fixture-key")
    monkeypatch.setenv("RSI_TARGET_MODEL", "target-default-model")
    monkeypatch.setenv("RSI_TARGET_BASE_URL", "https://target.example/v1")
    monkeypatch.setenv("RSI_TARGET_API_KEY", "target-fixture-key")
    monkeypatch.delenv("RSI_AGENT_MODELS", raising=False)


@pytest.mark.usefixtures("defaults")
def test_defaults_and_model_only_override(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AgentModelSettings.from_environment()
    for name in ("interaction-agent", "rsi-agent", "research-agent-1", "research-agent-2"):
        assert settings.resolve(name).model == "teacher-default-model"
    with pytest.raises(ModelConfigurationError, match="直接使用 RSI_TARGET"):
        settings.resolve("task-agent")
    monkeypatch.setenv("RSI_AGENT_MODELS", json.dumps({"rsi-agent": {"model": "rsi-agent-model"}}))
    settings = AgentModelSettings.from_environment()
    rsi_agent = settings.resolve("rsi-agent")
    assert rsi_agent.model == "rsi-agent-model"
    assert rsi_agent.base_url == "https://teacher.example/v1"
    assert rsi_agent.api_key == "teacher-fixture-key"
    assert settings.resolve("interaction-agent").model == "teacher-default-model"


@pytest.mark.usefixtures("defaults")
def test_all_research_agents_share_one_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESEARCH_CREDENTIAL", "research-fixture-key")
    monkeypatch.setenv(
        "RSI_AGENT_MODELS",
        json.dumps(
            {
                "research-agent": {
                    "model": "research-agent-model",
                    "base_url": "https://research.example/",
                    "api_key_env": "RESEARCH_CREDENTIAL",
                },
            }
        ),
    )
    settings = AgentModelSettings.from_environment()
    research_agent_1 = settings.resolve("research-agent-1")
    research_agent_2 = settings.resolve("research-agent-2")
    assert research_agent_1 == research_agent_2
    assert research_agent_1.model == "research-agent-model"
    assert research_agent_1.base_url == "https://research.example/v1"
    assert research_agent_1.api_key == "research-fixture-key"
    assert settings.resolve("rsi-agent").model == "teacher-default-model"
    assert settings.credential_env_names == {"RESEARCH_CREDENTIAL"}


@pytest.mark.usefixtures("defaults")
def test_changing_endpoint_never_leaks_default_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "RSI_AGENT_MODELS",
        json.dumps(
            {
                "interaction-agent": {"base_url": "https://another.example/v1"},
                "rsi-agent": {"base_url": "https://teacher.example/"},
            }
        ),
    )
    settings = AgentModelSettings.from_environment()
    assert settings.resolve("interaction-agent").api_key == ""
    assert settings.resolve("rsi-agent").api_key == "teacher-fixture-key"


@pytest.mark.usefixtures("defaults")
def test_key_reference_must_exist_and_can_explicitly_disable_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RSI_AGENT_MODELS", '{"rsi-agent":{"api_key_env":"EMPTY_MODEL_KEY"}}')
    monkeypatch.delenv("EMPTY_MODEL_KEY", raising=False)
    with pytest.raises(ModelConfigurationError, match="api_key_env"):
        AgentModelSettings.from_environment().resolve("rsi-agent")
    monkeypatch.setenv("EMPTY_MODEL_KEY", "")
    assert AgentModelSettings.from_environment().resolve("rsi-agent").api_key == ""


@pytest.mark.parametrize(
    "raw", ["bad-json", "[]", '{"rsi-agent":{"api_key":"secret"}}', '{"rsi-agent":{"model":1}}']
)
def test_invalid_overrides_do_not_echo_values(raw: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RSI_AGENT_MODELS", raw)
    with pytest.raises(ModelConfigurationError) as error:
        AgentModelSettings.from_environment()
    assert "secret" not in str(error.value)


@pytest.mark.usefixtures("defaults")
def test_per_line_research_override_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "RSI_AGENT_MODELS", '{"research-agent-3":{"model":"research-agent-3-model"}}'
    )
    with pytest.raises(ModelConfigurationError, match="只支持"):
        AgentModelSettings.from_environment().validate_agents()


@pytest.mark.usefixtures("defaults")
def test_task_agent_override_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RSI_AGENT_MODELS", '{"task-agent":{"model":"duplicate-model"}}')
    with pytest.raises(ModelConfigurationError, match="直接使用 RSI_TARGET"):
        AgentModelSettings.from_environment().validate_agents()
