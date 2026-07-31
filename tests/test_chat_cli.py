import json

from areal_rsi.agent import FakeRSIAgent
from areal_rsi.cli.main import _controller_from_rsi_config, main
from areal_rsi.config import RSIRunConfig
from areal_rsi.memory import Memory
from areal_rsi.services import AReaLService, EnvArenaClient, MockResearchAgent


def test_fake_agent_generates_start_loop_action():
    agent = FakeRSIAgent()

    response = agent.chat(
        "run ckpt_123 on swe_verified with sft for 2 iterations",
        controller=object(),
        memory=object(),
        workflow_spec={"harness": "mock"},
    )

    assert response.requires_confirmation is True
    assert response.actions[0].type == "start_loop"
    assert response.actions[0].args["initial_ckpt"] == "ckpt_123"
    assert response.actions[0].args["benchmark"] == "swe_verified"
    assert response.actions[0].args["max_iterations"] == 2


def test_rsi_config_initializes_configured_services_from_relative_paths(tmp_path, monkeypatch):
    areal_config = tmp_path / "areal.yaml"
    areal_config.write_text(
        f"""
areal:
  sft:
    scheduler_type: local
    commands: [areal, train, run]
    environment_variables: {{}}
""".lstrip(),
        encoding="utf-8",
    )
    envarena_config = tmp_path / "envarena.yaml"
    envarena_config.write_text(
        """
envarena:
  control_base_url_env: ARENA_CONTROL_URL
  token_env: ARENA_TOKEN
""".lstrip(),
        encoding="utf-8",
    )
    rsi_config = tmp_path / "rsi.yaml"
    rsi_config.write_text(
        """
run:
  initial_ckpt: ckpt_000
env:
  benchmark: swe_verified
services:
  areal:
    config_path: areal.yaml
  envarena:
    config_path: envarena.yaml
  research:
    backend: mock
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("ARENA_CONTROL_URL", "https://arena.example.test/openapi/v1")
    monkeypatch.setenv("ARENA_TOKEN", "token")

    controller = _controller_from_rsi_config(
        RSIRunConfig.load(rsi_config), tmp_path / "runs", Memory(tmp_path / "memory")
    )

    assert isinstance(controller.areal, AReaLService)
    assert isinstance(controller.envarena, EnvArenaClient)
    assert isinstance(controller.research_agent, MockResearchAgent)


def test_chat_cli_does_not_execute_confirmed_action_by_default(tmp_path, capsys):
    exit_code = main(
        [
            "chat",
            "run ckpt_000 on swe_verified",
            "--run-root",
            str(tmp_path),
            "--execute",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["executions"][0]["executed"] is False
    assert payload["executions"][0]["reason"] == "confirmation_required"


def test_chat_cli_executes_action_with_yes(tmp_path, capsys):
    exit_code = main(
        [
            "chat",
            "run ckpt_000 on swe_verified",
            "--run-root",
            str(tmp_path),
            "--execute",
            "--yes",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    execution = payload["executions"][0]
    assert execution["executed"] is True
    assert execution["result"]["status"] == "completed"
    assert execution["result"]["metadata"]["final_ckpt"] == "ckpt_000-sft-mock-sft-mock-sft-mock"


def test_chat_cli_executes_action_from_config(tmp_path, capsys):
    config_path = tmp_path / "rsi.yaml"
    config_path.write_text(
        """
run:
  initial_ckpt: ckpt_base
  max_iterations: 3
  method: sft
env:
  benchmark: base_task
  harness_config:
    harness: config_harness
""",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "chat",
            "--config",
            str(config_path),
            "run for 2 iterations",
            "--run-root",
            str(tmp_path),
            "--execute",
            "--yes",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    result = payload["executions"][0]["result"]
    assert result["status"] == "completed"
    assert result["metadata"]["initial_ckpt"] == "ckpt_base"
    assert result["metadata"]["benchmark"] == "base_task"
    assert result["metadata"]["harness_config"] == {"harness": "config_harness"}
    assert result["iteration"] == 2
    assert result["metadata"]["resolved_config"] == "resolved_rsi_config.json"


def test_chat_cli_without_message_starts_repl(tmp_path, capsys, monkeypatch):
    messages = iter(["run ckpt_000 on swe_verified for 1 iteration", "status", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(messages))

    exit_code = main(
        [
            "chat",
            "--run-root",
            str(tmp_path),
            "--execute",
            "--yes",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "AReaL-RSI chat started." in output
    assert "executed: run_id=" in output
    assert "当前状态是 completed" in output


def test_chat_cli_repl_accepts_chinese_exit(tmp_path, capsys, monkeypatch):
    messages = iter(["退出"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(messages))

    exit_code = main(["chat", "--run-root", str(tmp_path)])

    assert exit_code == 0
    assert "AReaL-RSI chat started." in capsys.readouterr().out


def test_chat_cli_repl_allows_no_action_reply(tmp_path, capsys, monkeypatch):
    messages = iter(["你是谁", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(messages))

    exit_code = main(["chat", "--run-root", str(tmp_path)])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "我是 RSIAgent" in output
    assert "executed:" not in output
    assert "action:" not in output


def test_run_loop_mock_cli(tmp_path, capsys):
    exit_code = main(
        [
            "run-loop-mock",
            "--ckpt",
            "ckpt_000",
            "--task-set",
            "swe_verified",
            "--max-iterations",
            "2",
            "--run-root",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "completed"
    assert payload["iteration"] == 2
    assert payload["metadata"]["final_ckpt"] == "ckpt_000-sft-mock-sft-mock"


def test_status_cli_prints_run_state(tmp_path, capsys):
    run_exit_code = main(
        [
            "run-loop-mock",
            "--ckpt",
            "ckpt_000",
            "--task-set",
            "swe_verified",
            "--max-iterations",
            "1",
            "--run-root",
            str(tmp_path),
        ]
    )
    assert run_exit_code == 0
    run_payload = json.loads(capsys.readouterr().out)

    status_exit_code = main(
        [
            "status",
            run_payload["run_id"],
            "--run-root",
            str(tmp_path),
        ]
    )

    assert status_exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_id"] == run_payload["run_id"]
    assert payload["status"] == "completed"
    assert payload["ckpt"] == "ckpt_000-sft-mock"


def test_run_config_cli(tmp_path, capsys):
    config_path = tmp_path / "rsi.yaml"
    config_path.write_text(
        """
run:
  initial_ckpt: ckpt_cfg
  max_iterations: 1
  method: sft
env:
  benchmark: cfg_task
  harness_config:
    harness: cfg_harness
""",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "run",
            "--config",
            str(config_path),
            "--run-root",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "completed"
    assert payload["iteration"] == 1
    assert payload["metadata"]["initial_ckpt"] == "ckpt_cfg"
    assert payload["metadata"]["benchmark"] == "cfg_task"
    assert payload["metadata"]["resolved_config"] == "resolved_rsi_config.json"
