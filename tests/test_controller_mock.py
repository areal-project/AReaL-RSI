from areal_rsi.controller import LocalRSIController
from areal_rsi.memory import Memory
from areal_rsi.schemas.actions import Action
from areal_rsi.services import MockAReaLService, MockEnvArenaClient, MockResearchAgent


def test_local_controller_runs_mock_iteration(tmp_path):
    memory = Memory(tmp_path / "memory")
    controller = LocalRSIController(
        run_root=tmp_path / "runs",
        areal=MockAReaLService(),
        envarena=MockEnvArenaClient(),
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
    assert (tmp_path / "memory" / "ledger.jsonl").exists()


def test_local_controller_get_status_loads_state(tmp_path):
    memory = Memory(tmp_path / "memory")
    controller = LocalRSIController(
        run_root=tmp_path / "runs",
        areal=MockAReaLService(),
        envarena=MockEnvArenaClient(),
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


def test_execute_action_accepts_harness_shortcut(tmp_path):
    memory = Memory(tmp_path / "memory")
    controller = LocalRSIController(
        run_root=tmp_path / "runs",
        areal=MockAReaLService(),
        envarena=MockEnvArenaClient(),
        research_agent=MockResearchAgent(),
        memory=memory,
    )

    result = controller.execute_action(
        Action(
            type="run_iteration",
            args={
                "ckpt": "ckpt_000",
                "task_set": "swe_verified",
                "method": "sft",
                "harness": "mock_harness",
            },
        )
    )

    assert result["status"] == "waiting_for_acceptance"
    assert result["metadata"]["harness_config"] == {"harness": "mock_harness"}
