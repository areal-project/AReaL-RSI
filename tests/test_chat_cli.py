import json

from areal_rsi.agent import FakeRSIAgent
from areal_rsi.cli.main import main


def test_fake_agent_generates_run_iteration_action():
    agent = FakeRSIAgent()

    response = agent.chat(
        "run ckpt_123 on swe_verified with sft",
        controller=object(),
        memory=object(),
        workflow_spec={"harness": "mock"},
    )

    assert response.requires_confirmation is True
    assert response.actions[0].type == "run_iteration"
    assert response.actions[0].args["ckpt"] == "ckpt_123"
    assert response.actions[0].args["task_set"] == "swe_verified"


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
    assert execution["result"]["status"] == "waiting_for_acceptance"
    assert execution["result"]["ckpt"] == "ckpt_000-sft-mock"


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
