"""Invariants for the small public API."""

from __future__ import annotations

import unittest
from dataclasses import fields
from inspect import signature

from areal_rsi.api import base as base_api
from areal_rsi.api import modules as modules_api
from areal_rsi.api import workflow as workflow_api
from areal_rsi.api.base import (
    TRAJECTORY_BUNDLE_MEDIA_TYPE,
    AgentSpec,
    AgentState,
    ArtifactChange,
    ArtifactVersion,
    BenchmarkSplit,
    ContentRef,
    EvaluationReport,
    EvolutionTarget,
    ExperimentPlan,
    ExperimentRun,
    HarnessChange,
    HarnessVersion,
    IterationState,
    ModelVersion,
    RecordRef,
    ResearchAgentState,
    ResearchIteration,
    RunState,
    SolutionVersion,
    Task,
    TaskResult,
    Trajectory,
    TrajectoryMaterialization,
    TrajectoryRef,
    TrajectoryStorageType,
    TrajectoryStoreSpec,
    VerificationRequest,
    VerificationStatus,
    VerifierSpec,
)
from areal_rsi.api.modules import (
    AgentRuntime,
    AgentSession,
    ArtifactBuilder,
    Benchmark,
    HarnessRuntime,
    TrainingBackend,
    TrajectoryNormalizer,
    TrajectorySource,
    TrajectoryStore,
    TrajectoryStoreFactory,
    Verifier,
    VerifierFactory,
)
from areal_rsi.api.workflow import ResearchAgent, RsiAgent, WorkflowContext


class InterfaceDesignTest(unittest.TestCase):
    def test_only_train_and_validate_splits_exist(self) -> None:
        self.assertEqual(
            list(BenchmarkSplit),
            [BenchmarkSplit.TRAIN, BenchmarkSplit.VALIDATE],
        )

    def test_artifact_is_a_first_class_evolution_target(self) -> None:
        self.assertIn(EvolutionTarget.ARTIFACT, list(EvolutionTarget))

        artifact = ArtifactVersion(
            version_id="game_0",
            name="game",
            content=ContentRef(uri="content://game", digest="sha256:game"),
            created_at="2026-09-04T00:00:00Z",
        )
        solution = SolutionVersion(
            solution_id="solution_0",
            model=self._model(),
            harness=self._harness(),
            artifacts=(artifact,),
            created_at="2026-09-04T00:00:00Z",
        )
        change = ArtifactChange(
            artifact_name="game",
            patch=ContentRef(uri="content://game-patch", digest="sha256:patch"),
            reason="improve gameplay",
        )

        self.assertEqual(solution.artifacts[0].name, change.artifact_name)
        self.assertIsInstance(artifact.content, ContentRef)
        self.assertFalse(hasattr(base_api, "ArtifactRef"))
        self.assertFalse(hasattr(modules_api, "ArtifactStore"))

    def test_task_never_contains_hidden_expected_data(self) -> None:
        self.assertNotIn("expected", {item.name for item in fields(Task)})
        self.assertIn("list_tasks", Benchmark.__dict__)
        self.assertNotIn("list_cases", Benchmark.__dict__)

    def test_removed_internal_harness_models_stay_out_of_public_api(self) -> None:
        self.assertFalse(hasattr(base_api, "VersionOrigin"))
        self.assertFalse(hasattr(base_api, "HarnessResource"))
        self.assertFalse(hasattr(base_api, "HarnessManifest"))
        self.assertFalse(hasattr(base_api, "HarnessEdit"))
        self.assertFalse(hasattr(base_api, "BenchmarkCase"))

    def test_harness_version_represents_the_complete_snapshot(self) -> None:
        field_names = {item.name for item in fields(HarnessVersion)}

        self.assertEqual(
            field_names,
            {"version_id", "name", "content", "created_at", "parent_version_id"},
        )

    def test_harness_change_is_one_patch(self) -> None:
        field_names = {item.name for item in fields(HarnessChange)}

        self.assertEqual(field_names, {"patch", "reason"})

    def test_each_research_agent_owns_its_iterations(self) -> None:
        plan = self._plan(research_agent_count=2)
        run = ExperimentRun(
            run_id="run_001",
            plan=plan,
            state=RunState.RUNNING,
            best_solution_id="solution_0",
            research_agents=(self._researcher(1), self._researcher(2)),
        )

        self.assertEqual([item.agent_id for item in run.research_agents], ["agent_1", "agent_2"])
        self.assertEqual(
            [[step.iteration_id for step in item.iterations] for item in run.research_agents],
            [["agent_1-1", "agent_1-2"], ["agent_2-1", "agent_2-2"]],
        )
        agent_state_fields = {item.name for item in fields(ResearchAgentState)}
        self.assertNotIn("current_solution_id", agent_state_fields)
        self.assertNotIn("best_solution_id", agent_state_fields)

    def test_research_agent_uses_direct_inputs_and_history_selection(self) -> None:
        self.assertFalse(hasattr(base_api, "ResearchInput"))
        self.assertFalse(hasattr(base_api, "SelectionInput"))
        self.assertFalse(hasattr(base_api, "Selection"))
        self.assertFalse(hasattr(base_api, "ResearchAction"))
        self.assertIn("propose", ResearchAgent.__dict__)
        self.assertIn("select", ResearchAgent.__dict__)
        self.assertEqual(
            list(signature(ResearchAgent.propose).parameters),
            ["self", "goal", "target", "current", "report", "trajectories"],
        )
        self.assertEqual(
            list(signature(ResearchAgent.select).parameters),
            ["self", "current_solution_id", "candidates"],
        )

    def test_evaluation_report_contains_aggregates_only(self) -> None:
        field_names = {item.name for item in fields(EvaluationReport)}

        self.assertNotIn("summary", field_names)
        self.assertNotIn("regressions", field_names)
        self.assertNotIn("trajectories", field_names)
        self.assertNotIn("evaluation_id", field_names)

    def test_trajectory_only_indexes_the_complete_trace(self) -> None:
        field_names = {item.name for item in fields(Trajectory)}

        self.assertNotIn("state", field_names)
        self.assertNotIn("output", field_names)
        self.assertNotIn("attachments", field_names)
        self.assertIn("content", field_names)

    def test_research_iteration_has_no_task_or_output_taxonomy(self) -> None:
        node = ResearchIteration("i1", "run1", "agent1", RecordRef("run://run1/assignment.json"))
        self.assertEqual(node.state, IterationState.PENDING)
        self.assertEqual(node.references, ())
        node.state = IterationState.COMPLETED
        self.assertIsNone(node.report_ref)
        names = {item.name for item in fields(ResearchIteration)}
        self.assertTrue(
            names.isdisjoint(
                {
                    "task_type",
                    "needs_verify",
                    "train_report",
                    "validation_report",
                    "candidate_solution_id",
                    "next_solution_id",
                    "change",
                }
            )
        )

    def test_public_dataclasses_are_not_frozen(self) -> None:
        version = self._harness()

        self.assertNotIn("metadata", {item.name for item in fields(HarnessVersion)})
        version.name = "changed"
        self.assertEqual(version.name, "changed")
        for module in (base_api, workflow_api):
            for value in vars(module).values():
                if isinstance(value, type) and "__dataclass_params__" in vars(value):
                    self.assertFalse(vars(value)["__dataclass_params__"].frozen)

    def test_trajectory_bundle_has_a_standard_contract(self) -> None:
        self.assertEqual(
            TRAJECTORY_BUNDLE_MEDIA_TYPE,
            "application/vnd.areal-rsi.trajectories+json",
        )
        self.assertIn("bundle", TrajectoryStore.__dict__)
        self.assertIn("query", TrajectoryStore.__dict__)
        self.assertIn("create", TrajectoryStoreFactory.__dict__)
        self.assertIn("fetch", TrajectorySource.__dict__)
        self.assertIn("normalize", TrajectoryNormalizer.__dict__)

    def test_trajectory_store_spec_describes_local_and_remote_storage(self) -> None:
        local = TrajectoryStoreSpec(
            store_id="local",
            provider="filesystem",
            storage_type=TrajectoryStorageType.LOCAL,
            uri="./runs",
        )
        remote = TrajectoryStoreSpec(
            store_id="remote",
            provider="external",
            storage_type=TrajectoryStorageType.REMOTE,
            uri="trajectory://project",
            materialization=TrajectoryMaterialization.REFERENCE,
        )

        self.assertEqual(local.materialization, TrajectoryMaterialization.SNAPSHOT)
        self.assertEqual(remote.storage_type, TrajectoryStorageType.REMOTE)

    def test_agent_runtime_uses_a_multi_turn_session(self) -> None:
        spec_fields = {item.name for item in fields(AgentSpec)}

        self.assertEqual(
            spec_fields,
            {
                "agent_id",
                "runtime",
                "model",
                "prompt_templates",
                "skills",
                "tools",
                "mcp_servers",
            },
        )
        self.assertNotIn("config", spec_fields)
        self.assertFalse(hasattr(base_api, "AgentInput"))
        self.assertFalse(hasattr(base_api, "AgentOutput"))
        self.assertIn("open", AgentRuntime.__dict__)
        self.assertNotIn("run", AgentRuntime.__dict__)
        self.assertIn("send", AgentSession.__dict__)
        self.assertIn("finish", AgentSession.__dict__)

    def test_task_runtime_and_builders_are_abstract(self) -> None:
        self.assertEqual({"run"} & HarnessRuntime.__dict__.keys(), {"run"})
        self.assertIn("train", TrainingBackend.__dict__)
        self.assertIn("build", ArtifactBuilder.__dict__)
        self.assertIn("prune", RsiAgent.__dict__)

    def test_verifier_receives_one_structured_request(self) -> None:
        result = TaskResult(
            output=ContentRef(uri="content://task-output", digest="sha256:output"),
            trajectory=TrajectoryRef("trajectory_1"),
        )
        request = VerificationRequest(
            task=Task(task_id="task_1", input={"prompt": "question"}),
            solution=self._solution(),
            result=result,
            verifier=VerifierSpec(
                verifier_id="user-verifier",
                provider="python",
                entrypoint="user.verifier:Verifier",
            ),
        )

        self.assertEqual(result.output.uri, "content://task-output")
        self.assertEqual(request.result.trajectory.trajectory_id, "trajectory_1")
        self.assertEqual(
            list(signature(Verifier.verify).parameters),
            ["self", "request"],
        )
        self.assertIn("create", VerifierFactory.__dict__)
        self.assertEqual(VerificationStatus.SUCCEEDED, "succeeded")

    def test_rsi_chat_and_workflow_have_no_wrapper_results(self) -> None:
        self.assertFalse(hasattr(base_api, "ConversationInput"))
        self.assertFalse(hasattr(base_api, "ConversationOutput"))
        self.assertFalse(hasattr(workflow_api, "WorkflowResult"))
        self.assertEqual(list(signature(RsiAgent.chat).parameters), ["self", "message"])

    def test_only_the_selected_evolution_backend_is_required(self) -> None:
        context_fields = {item.name: item for item in fields(WorkflowContext)}

        self.assertIsNone(context_fields["harness_builder"].default)
        self.assertIsNone(context_fields["training_backend"].default)
        self.assertIsNone(context_fields["artifact_builder"].default)

    @staticmethod
    def _plan(*, research_agent_count: int) -> ExperimentPlan:
        return ExperimentPlan(
            goal="improve harness",
            workflow="harness-evolution",
            benchmark="kernelbench",
            baseline_solution_id="solution_0",
            target=EvolutionTarget.HARNESS,
            max_iterations=2,
            research_agent_count=research_agent_count,
        )

    @staticmethod
    def _model() -> ModelVersion:
        return ModelVersion(
            version_id="model_0",
            name="model",
            content=ContentRef(uri="content://model", digest="sha256:model"),
            created_at="2026-09-04T00:00:00Z",
        )

    @staticmethod
    def _harness() -> HarnessVersion:
        return HarnessVersion(
            version_id="harness_0",
            name="harness",
            content=ContentRef(uri="content://harness", digest="sha256:harness"),
            created_at="2026-09-04T00:00:00Z",
        )

    @classmethod
    def _solution(cls) -> SolutionVersion:
        return SolutionVersion(
            solution_id="solution_0",
            model=cls._model(),
            harness=cls._harness(),
            created_at="2026-09-04T00:00:00Z",
        )

    @staticmethod
    def _report(solution_id: str, split: BenchmarkSplit) -> EvaluationReport:
        return EvaluationReport(
            solution_id=solution_id,
            split=split,
            passed=False,
            score=0.0,
            metrics={},
            created_at="2026-09-04T00:00:00Z",
        )

    @classmethod
    def _researcher(cls, index: int) -> ResearchAgentState:
        iterations = tuple(
            ResearchIteration(
                iteration_id=f"agent_{index}-{iteration}",
                run_id="run_001",
                agent_id=f"agent_{index}",
                assignment_ref=RecordRef(f"run://run_001/assignments/{index}-{iteration}.json"),
                state=IterationState.COMPLETED,
                created_at="2026-09-04T00:00:00Z",
                updated_at="2026-09-04T00:01:00Z",
            )
            for iteration in range(1, 3)
        )
        return ResearchAgentState(
            agent_id=f"agent_{index}",
            state=AgentState.RUNNING,
            iterations=iterations,
        )


if __name__ == "__main__":
    unittest.main()
