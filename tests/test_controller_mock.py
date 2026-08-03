import json

from areal_rsi.config import RSIRunConfig
import areal_rsi.controller.local_controller as controller_module
from areal_rsi.controller import LocalRSIController
from areal_rsi.memory import Memory
from areal_rsi.memory.ledger import read_ledger
from areal_rsi.schemas.actions import Action
from areal_rsi.services import MockAReaLService, MockEnvClient, MockResearchAgent


def test_local_controller_runs_mock_iteration(tmp_path):
    controller = LocalRSIController(
        run_root=tmp_path / "runs",
        areal=MockAReaLService(),
        envarena=MockEnvClient(),
        research_agent=MockResearchAgent(),
        memory=Memory(tmp_path / "legacy-global-memory"),
    )

    state = controller.run_iteration(
        ckpt="ckpt_000",
        benchmark="swe_verified",
        harness_config={"harness": "mock"},
        method="sft",
    )

    assert state.status == "waiting_for_acceptance"
    assert state.ckpt == "ckpt_000-sft-mock"
    assert "record_decision_pending" in state.completed_steps
    run_dir = tmp_path / "runs" / state.run_id
    assert state.iteration == 1
    assert (run_dir / "state.json").exists()
    assert (run_dir / "artifacts" / "dataset.json").exists()
    assert (run_dir / "audit.jsonl").exists()
    assert (run_dir / "memory" / "ledger.jsonl").exists()
    assert not (tmp_path / "legacy-global-memory" / "ledger.jsonl").exists()

    audit_path = run_dir / "audit.jsonl"
    assert "mock-api-key" not in audit_path.read_text(encoding="utf-8")

    audit_events = read_ledger(audit_path)
    succeeded = [event for event in audit_events if event["status"] == "succeeded"]
    assert [event["step"] for event in succeeded] == [
        "holdout_eval",
        "eval",
        "collect_trajs",
        "generate_experiences",
        "skill_eval",
        "generate_dataset",
        "train",
        "record_decision_pending",
    ]
    assert {event["event_type"] for event in succeeded} == {"workflow_step"}
    assert succeeded[0]["output"]["score"]["mean_reward"] == 0.5
    assert succeeded[2]["output"]["score"]["mean_reward"] == 0.5
    assert succeeded[4]["output"]["score"]["mean_reward"] == 1.0
    assert succeeded[6]["output"]["ckpt"] == "ckpt_000-sft-mock"

    distilled = json.loads(
        (run_dir / "artifacts" / "distilled_trajs.json").read_text(encoding="utf-8")
    )
    dataset = json.loads(
        (run_dir / "artifacts" / "dataset.json").read_text(encoding="utf-8")
    )
    assert "Preserve the successful solution pattern" in distilled[0]["messages"][1]["content"]
    assert dataset["examples"][0]["messages"][1]["content"] == (
        "Solve swe_verified_task_0."
    )
    assert all("experience" not in example for example in dataset["examples"])

    memory_events = read_ledger(run_dir / "memory" / "ledger.jsonl")
    assert "eval_summarized" in [event["title"] for event in memory_events]
    assert "holdout_summarized" in [event["title"] for event in memory_events]


def test_file_backed_dataset_reports_validated_record_count():
    assert controller_module._dataset_num_examples(
        {
            "examples": [],
            "metadata": {"validation": {"record_count": 1}},
        }
    ) == 1


def test_artifact_ref_normalizes_symlinked_run_root(tmp_path):
    actual = tmp_path / "actual"
    artifact = actual / "artifacts" / "skill.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("{}", encoding="utf-8")
    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)

    assert controller_module._artifact_ref(linked, artifact) == "artifacts/skill.json"


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
                "benchmark": "swe_verified",
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
                "benchmark": "swe_verified",
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
    controller = LocalRSIController(
        run_root=tmp_path / "runs",
        areal=MockAReaLService(),
        envarena=MockEnvClient(),
        research_agent=MockResearchAgent(),
    )

    state = controller.run_loop(
        initial_ckpt="ckpt_000",
        benchmark="swe_verified",
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
    assert [
        result["iteration"] for result in state.metadata["iteration_results"]
    ] == [1, 2]
    run_dir = tmp_path / "runs" / state.run_id
    assert (run_dir / "artifacts" / "iter_001" / "dataset.json").exists()
    assert (run_dir / "artifacts" / "iter_002" / "dataset.json").exists()

    audit_events = read_ledger(run_dir / "audit.jsonl")
    succeeded = [event for event in audit_events if event["status"] == "succeeded"]
    workflow_steps = [
        event for event in succeeded if event["event_type"] == "workflow_step"
    ]
    loop_decisions = [
        event for event in succeeded if event["event_type"] == "loop_decision"
    ]
    user_goals = [
        event for event in succeeded if event["event_type"] == "user_goal_received"
    ]
    assert len(workflow_steps) == 16
    assert {event["iteration"] for event in workflow_steps} == {1, 2}
    assert len(loop_decisions) == 2
    assert len(user_goals) == 1

    memory_events = read_ledger(run_dir / "memory" / "ledger.jsonl")
    assert "run_completed" in [event["title"] for event in memory_events]

    status = controller.get_status(state.run_id)
    assert status.run_id == state.run_id
    assert status.status == "completed"
    assert status.ckpt == state.ckpt


def test_memory_and_research_context_are_isolated_per_run(tmp_path):
    class CapturingResearcher(MockResearchAgent):
        def __init__(self):
            self.contexts = []

        def generate_dataset(self, trajs, memory_context, current_ckpt, **kwargs):
            if kwargs.get("config", {}).get("stage") == "experience_generation":
                self.contexts.append(memory_context)
            return super().generate_dataset(
                trajs, memory_context, current_ckpt, **kwargs
            )

    researcher = CapturingResearcher()
    controller = LocalRSIController(
        run_root=tmp_path / "runs",
        areal=MockAReaLService(),
        envarena=MockEnvClient(),
        research_agent=researcher,
        memory=Memory(tmp_path / "global-memory-must-not-be-used"),
    )

    first = controller.run_loop(
        initial_ckpt="ckpt_first",
        benchmark="swe_verified",
        harness_config={"harness": "mock"},
        max_iterations=2,
    )
    second = controller.run_loop(
        initial_ckpt="ckpt_second",
        benchmark="swe_verified",
        harness_config={"harness": "mock"},
        max_iterations=1,
    )

    first_iter_one, first_iter_two, second_iter_one = researcher.contexts
    assert "iteration_finished" not in [
        event["title"] for event in first_iter_one["events"]
    ]
    assert "iteration_finished" in [
        event["title"] for event in first_iter_two["events"]
    ]
    assert {
        event["metadata"]["run_id"] for event in second_iter_one["events"]
    } == {second.run_id}
    assert first.run_id != second.run_id
    assert (tmp_path / "runs" / first.run_id / "memory" / "ledger.jsonl").exists()
    assert (tmp_path / "runs" / second.run_id / "memory" / "ledger.jsonl").exists()
    assert not (tmp_path / "global-memory-must-not-be-used" / "ledger.jsonl").exists()


def test_mock_eval_model_reference_matches_researcher_input(tmp_path):
    class CapturingResearcher(MockResearchAgent):
        def generate_dataset(self, trajs, memory_context, current_ckpt, **kwargs):
            self.trajs = trajs
            self.model_ref = kwargs["model_ref"]
            self.current_ckpt = current_ckpt
            return super().generate_dataset(
                trajs, memory_context, current_ckpt, **kwargs
            )

    researcher = CapturingResearcher()
    controller = LocalRSIController(
        run_root=tmp_path / "runs",
        areal=MockAReaLService(),
        envarena=MockEnvClient(),
        research_agent=researcher,
        memory=Memory(tmp_path / "memory"),
    )

    controller.run_iteration(
        ckpt="/models/checkpoint-7",
        benchmark="swe_verified",
        harness_config={"harness": "mock"},
    )

    assert researcher.current_ckpt == "/models/checkpoint-7"
    assert researcher.model_ref["model_path"] == "/models/checkpoint-7"
    assert {traj["model_ref"] for traj in researcher.trajs} == {
        "/models/checkpoint-7"
    }


def test_controller_polls_eval_without_changing_envclient_interface(tmp_path):
    class AsyncMockEnvClient(MockEnvClient):
        def __init__(self):
            super().__init__()
            self.progress_calls = {}

        def get_eval_progress(self, job):
            job_id = job["eval_job_id"]
            calls = self.progress_calls.get(job_id, 0) + 1
            self.progress_calls[job_id] = calls
            return {"status": "RUNNING" if calls == 1 else "DONE"}

    envarena = AsyncMockEnvClient()
    controller = LocalRSIController(
        run_root=tmp_path / "runs",
        areal=MockAReaLService(),
        envarena=envarena,
        research_agent=MockResearchAgent(),
        memory=Memory(tmp_path / "memory"),
    )

    state = controller.run_iteration(
        ckpt="ckpt_000",
        benchmark="swe_verified",
        harness_config={
            "harness": "mock",
            "job_config": {"poll_interval_seconds": 0},
        },
    )

    assert state.status == "waiting_for_acceptance"
    assert sorted(envarena.progress_calls.values()) == [2, 2, 2]


def test_target_score_stops_successfully_and_max_iterations_exhausts(tmp_path):
    controller = LocalRSIController(
        run_root=tmp_path / "runs",
        areal=MockAReaLService(),
        envarena=MockEnvClient(),
        research_agent=MockResearchAgent(),
        memory=Memory(tmp_path / "memory"),
    )

    reached = controller.run_loop(
        initial_ckpt="ckpt_target",
        benchmark="swe_verified",
        harness_config={"harness": "mock"},
        max_iterations=3,
        stop_criteria={"target_mean_reward": 0.5},
    )
    exhausted = controller.run_loop(
        initial_ckpt="ckpt_exhaust",
        benchmark="swe_verified",
        harness_config={"harness": "mock"},
        max_iterations=2,
        stop_criteria={"target_mean_reward": 0.9},
    )

    assert reached.iteration == 1
    assert reached.ckpt == "ckpt_target"
    assert reached.train_run is None
    assert reached.metadata["goal_reached"] is True
    assert reached.metadata["stop_reason"] == "target_mean_reward_reached"
    reached_run = tmp_path / "runs" / reached.run_id
    assert not (reached_run / "artifacts" / "iter_001" / "dataset.json").exists()
    assert exhausted.iteration == 2
    assert exhausted.metadata["goal_reached"] is False
    assert exhausted.metadata["stop_reason"] == "max_iterations_exhausted"


def test_controller_waits_for_slurm_and_collects_checkpoint(tmp_path, monkeypatch):
    class SlurmAReaL:
        def start_experiment(self, config):
            self.config = config
            return {
                "job_id": "42",
                "status": "submitted",
                "train_run_id": "exp:trial",
            }

        def get_experiment_artifacts(self, config):
            assert config is self.config
            return {"latest_checkpoint": "/checkpoints/epoch1globalstep1"}

    monkeypatch.setattr(
        controller_module, "_slurm_job_state", lambda job_id: "COMPLETED"
    )
    controller = LocalRSIController(
        run_root=tmp_path / "runs",
        areal=SlurmAReaL(),
        envarena=MockEnvClient(),
        research_agent=MockResearchAgent(),
        memory=Memory(tmp_path / "memory"),
    )

    state = controller.run_iteration(
        ckpt="ckpt_000",
        benchmark="swe_verified",
        harness_config={"harness": "mock"},
        train_config={"poll_interval_seconds": 0},
    )

    assert state.train_run["metadata"]["status"] == "completed"
    assert state.ckpt == "/checkpoints/epoch1globalstep1"


def test_execute_action_starts_loop_from_config(tmp_path):
    memory = Memory(tmp_path / "memory")
    controller = LocalRSIController(
        run_root=tmp_path / "runs",
        areal=MockAReaLService(),
        envarena=MockEnvClient(),
        research_agent=MockResearchAgent(),
        memory=memory,
    )
    base_config = RSIRunConfig.from_dict(
        {
            "run": {"initial_ckpt": "ckpt_000", "max_iterations": 3, "method": "sft"},
            "env": {"benchmark": "swe_verified", "harness_config": {"harness": "mock"}},
        }
    )

    result = controller.execute_action(
        Action(
            type="start_loop",
            args={"overrides": {"run.max_iterations": 1, "env.benchmark": "swe_small"}},
            metadata={"base_config": base_config},
        )
    )

    assert result["status"] == "completed"
    assert result["iteration"] == 1
    assert result["metadata"]["benchmark"] == "swe_small"
    resolved_config = tmp_path / "runs" / result["run_id"] / "resolved_rsi_config.json"
    assert resolved_config.exists()

    audit_events = read_ledger(tmp_path / "runs" / result["run_id"] / "audit.jsonl")
    assert audit_events[0]["event_type"] == "run_config_resolved"
    assert audit_events[0]["input"]["overrides"] == {
        "run.max_iterations": 1,
        "env.benchmark": "swe_small",
    }
