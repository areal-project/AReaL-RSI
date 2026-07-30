from areal_rsi.controller import LocalRSIController
from areal_rsi.memory import Memory
from areal_rsi.memory.ledger import read_ledger
from areal_rsi.schemas.actions import Action
from areal_rsi.services import MockAReaLService, MockEnvClient, MockResearchAgent


def test_local_controller_runs_mock_iteration(tmp_path):
    memory = Memory(tmp_path / "memory")
    controller = LocalRSIController(
        run_root=tmp_path / "runs",
        areal=MockAReaLService(),
        envarena=MockEnvClient(),
        research_agent=MockResearchAgent(),
        memory=memory,
    )

    state = controller.run_iteration(
        ckpt="ckpt_000",
        task_set="swe_verified",
        harness_config={"harness": "mock"},
        method="sft",
    )

    assert state.status == "waiting_for_acceptance"
    assert state.ckpt == "ckpt_000-sft-mock"
    assert "record_decision_pending" in state.completed_steps
    assert (tmp_path / "runs" / state.run_id / "state.json").exists()
    assert (tmp_path / "runs" / state.run_id / "artifacts" / "dataset.json").exists()
    assert (tmp_path / "runs" / state.run_id / "audit.jsonl").exists()
    assert (tmp_path / "memory" / "ledger.jsonl").exists()

    audit_path = tmp_path / "runs" / state.run_id / "audit.jsonl"
    assert "mock-api-key" not in audit_path.read_text(encoding="utf-8")

    audit_events = read_ledger(audit_path)
    succeeded = [event for event in audit_events if event["status"] == "succeeded"]
    assert [event["step"] for event in succeeded] == [
        "ensure_rollout_endpoint",
        "register_model",
        "eval",
        "collect_trajs",
        "generate_dataset",
        "train",
        "update_endpoint",
        "holdout_eval",
        "record_decision_pending",
    ]
    assert {event["event_type"] for event in succeeded} == {"workflow_step"}
    assert succeeded[3]["output"]["score"]["mean_reward"] == 0.5
    assert succeeded[5]["output"]["ckpt"] == "ckpt_000-sft-mock"

    memory_events = read_ledger(tmp_path / "memory" / "ledger.jsonl")
    assert "eval_summarized" in [event["title"] for event in memory_events]
    assert "holdout_summarized" in [event["title"] for event in memory_events]


def test_local_controller_get_status_loads_state(tmp_path):
    memory = Memory(tmp_path / "memory")
    controller = LocalRSIController(
        run_root=tmp_path / "runs",
        areal=MockAReaLService(),
        envarena=MockEnvClient(),
        research_agent=MockResearchAgent(),
        memory=memory,
    )
    state = controller.run_iteration(
        ckpt="ckpt_000",
        task_set="swe_verified",
        harness_config={"harness": "mock"},
    )

    loaded = controller.get_status(state.run_id)

    assert loaded.run_id == state.run_id
    assert loaded.status == "waiting_for_acceptance"


def test_execute_action_starts_loop_with_harness_shortcut(tmp_path):
    memory = Memory(tmp_path / "memory")
    controller = LocalRSIController(
        run_root=tmp_path / "runs",
        areal=MockAReaLService(),
        envarena=MockEnvClient(),
        research_agent=MockResearchAgent(),
        memory=memory,
    )

    result = controller.execute_action(
        Action(
            type="start_loop",
            args={
                "ckpt": "ckpt_000",
                "task_set": "swe_verified",
                "method": "sft",
                "harness": "mock_harness",
                "max_iterations": 1,
            },
        )
    )

    assert result["status"] == "completed"
    assert result["metadata"]["harness_config"] == {"harness": "mock_harness"}


def test_execute_action_starts_loop_with_checkpoint_alias(tmp_path):
    memory = Memory(tmp_path / "memory")
    controller = LocalRSIController(
        run_root=tmp_path / "runs",
        areal=MockAReaLService(),
        envarena=MockEnvClient(),
        research_agent=MockResearchAgent(),
        memory=memory,
    )

    result = controller.execute_action(
        Action(
            type="start_loop",
            args={
                "checkpoint": "ckpt_000",
                "dataset": "swe_verified",
                "method": "sft",
                "harness": "mock_harness",
                "iterations": 1,
                "notes": "ignored user-facing context",
            },
        )
    )

    assert result["status"] == "completed"
    assert result["metadata"]["initial_ckpt"] == "ckpt_000"


def test_local_controller_runs_mock_loop(tmp_path):
    memory = Memory(tmp_path / "memory")
    controller = LocalRSIController(
        run_root=tmp_path / "runs",
        areal=MockAReaLService(),
        envarena=MockEnvClient(),
        research_agent=MockResearchAgent(),
        memory=memory,
    )

    state = controller.run_loop(
        initial_ckpt="ckpt_000",
        task_set="swe_verified",
        harness_config={"harness": "mock"},
        method="sft",
        max_iterations=2,
    )

    assert state.status == "completed"
    assert state.iteration == 2
    assert state.ckpt == "ckpt_000-sft-mock-sft-mock"
    assert state.metadata["initial_ckpt"] == "ckpt_000"
    assert state.metadata["final_ckpt"] == "ckpt_000-sft-mock-sft-mock"
    assert state.metadata["accepted_ckpts"] == [
        "ckpt_000",
        "ckpt_000-sft-mock",
        "ckpt_000-sft-mock-sft-mock",
    ]
    assert [result["iteration"] for result in state.metadata["iteration_results"]] == [1, 2]
    assert (tmp_path / "runs" / state.run_id / "artifacts" / "iter_001" / "dataset.json").exists()
    assert (tmp_path / "runs" / state.run_id / "artifacts" / "iter_002" / "dataset.json").exists()

    audit_events = read_ledger(tmp_path / "runs" / state.run_id / "audit.jsonl")
    succeeded = [event for event in audit_events if event["status"] == "succeeded"]
    workflow_steps = [event for event in succeeded if event["event_type"] == "workflow_step"]
    loop_decisions = [event for event in succeeded if event["event_type"] == "loop_decision"]
    user_goals = [event for event in succeeded if event["event_type"] == "user_goal_received"]
    assert len(workflow_steps) == 18
    assert {event["iteration"] for event in workflow_steps} == {1, 2}
    assert len(loop_decisions) == 2
    assert len(user_goals) == 1

    memory_events = read_ledger(tmp_path / "memory" / "ledger.jsonl")
    assert "loop_completed" in [event["title"] for event in memory_events]
