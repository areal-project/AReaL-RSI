"""Shared research lifecycle through real CPU workers; no model/GPU results are claimed."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from fake_codex import FakeCodex
from test_session import eventually
from test_training import git_repository

from areal_rsi.api.control import CandidateProposal, encode_record
from areal_rsi.api.execution import JobSpec
from areal_rsi.api.records import (
    ArtifactVersion,
    ContentRef,
    HarnessVersion,
    ModelVersion,
    SolutionVersion,
)
from areal_rsi.api.research import (
    CandidateContext,
    CandidatePlan,
    CandidateStatus,
    ComparisonContract,
    EvaluationContext,
    EvaluationPlan,
    ProblemBinding,
    ProblemSpec,
    SessionConfig,
    SessionPhase,
    SessionStatus,
    SourceTree,
)
from areal_rsi.infra.coordination import ProblemRun, pin_problem
from areal_rsi.infra.execution.jobs import JobExecutionError
from areal_rsi.infra.session import SessionSystem
from areal_rsi.infra.storage.artifacts import file_ref, snapshot_directory, write_record


@dataclass(frozen=True)
class TextConfig:
    gate: str = ""
    min_rounds: int = 1
    source: str = ""


@dataclass(frozen=True)
class TextRecipe:
    answer: str


class TextProblem:
    """An actual text-artifact task, exercising the optional-training path."""

    config_type = TextConfig
    recipe_type = TextRecipe

    def __init__(self, config: TextConfig):
        self.config = config

    def prepare(self, root: Path) -> ProblemSpec:
        model = root / "unused-model"
        model.mkdir(exist_ok=True)
        (model / "config.json").write_text("{}")
        manifest = snapshot_directory(model, root / "model.json")
        answer = root / "base.txt"
        answer.write_text("wrong")
        ref = file_ref(answer)
        baseline = SolutionVersion(
            "base",
            ModelVersion("model", "unused", ContentRef(model.as_uri(), manifest.digest), "fixture"),
            HarnessVersion("harness", "text", ref, "fixture"),
            "fixture",
            (ArtifactVersion("base-answer", "answer", ref, "fixture"),),
        )
        return ProblemSpec(
            "text",
            "Write the exact text correct.",
            baseline,
            manifest,
            ComparisonContract(min_rounds=self.config.min_rounds, target_delta=0.5),
            sources=(SourceTree(self.config.source, ("policy.py",)),) if self.config.source else (),
        )

    def build_candidate(self, context: CandidateContext, recipe: TextRecipe) -> CandidatePlan:
        if recipe.answer == "invalid-generator":
            raise ValueError("Generator failed before producing a candidate")
        path = Path(context.root) / "candidates" / context.candidate_id / "answer.txt"
        path.write_text(recipe.answer)
        ref = file_ref(path)
        return CandidatePlan(
            replace(
                context.parent,
                solution_id=context.candidate_id,
                parent_solution_id=context.parent.solution_id,
                artifacts=(ArtifactVersion(context.candidate_id, "answer", ref, "fixture"),),
            ),
            (ref,),
        )

    def build_evaluation(self, context: EvaluationContext) -> EvaluationPlan:
        ref = context.solution.artifacts[0].content
        answer = Path(ref.uri.removeprefix("file://"))
        output = Path(context.output)
        script = (
            "import json,time; from pathlib import Path; "
            f"gate=Path({self.config.gate!r}); "
            f"answer=Path({str(answer)!r}).read_text(); "
            f"output=Path({str(output)!r}); "
            "(output/'started').write_text('started'); "
            "exec('while gate.is_file():\\n time.sleep(0.01)'); "
            "passed=answer=='correct'; "
            "sample=dict(task_id='question',output=answer,score=float(passed),passed=passed,"
            "events=[dict(kind='generation',content=answer)],metrics={}); "
            "(output/'samples.jsonl').write_text(json.dumps(sample)+'\\n')"
        )
        return EvaluationPlan(
            JobSpec((sys.executable, "-c", script), timeout_seconds=15), ("question",), (ref,)
        )


def problem_run(tmp_path: Path, config: TextConfig | None = None) -> ProblemRun:
    config_ref = write_record(tmp_path / "input.json", config or TextConfig())
    root = tmp_path / "problem"
    binding = pin_problem(ProblemBinding("test_problems:TextProblem", config_ref), root)
    run = ProblemRun("run-fixture", root, binding)
    run.prepare()
    return run


def test_artifact_research_selection_and_final_evaluation_survive_reopen(tmp_path: Path) -> None:
    run = problem_run(tmp_path)

    async def execute() -> None:
        await run.evaluate("base")
        recipe = tmp_path / "recipe.json"
        write_record(recipe, TextRecipe("correct"))
        run.create_candidate("r1", "base", "Use the exact answer", recipe)
        result = await run.evaluate("r1")
        assert result.training is None and result.validation and result.validation.report.score == 1
        assert run.create_candidate("r1", "base", "Use the exact answer", recipe) == result
        with pytest.raises(ValueError, match="different proposal"):
            run.create_candidate("r1", "base", "Changed hypothesis", recipe)
        run.select("r1")
        with pytest.raises(ValueError, match="immutable"):
            run.select("other")
        with pytest.raises(ValueError, match="frozen"):
            await run.evaluate("r1")
        reopened = ProblemRun(run.run_id, run.root, run.binding)
        final = await reopened.finalize("r1")
        assert final.improvement == 1 and final.target_met
        assert final.comparison and final.comparison.count == 1
        assert await reopened.finalize("r1") == final
        assert (run.root / "report.md").is_file()
        assert len(list((run.root / "execution").rglob("job.json"))) == 4

    asyncio.run(execute())


def test_cached_evidence_and_frozen_protocol_cannot_be_silently_replaced(tmp_path: Path) -> None:
    run = problem_run(tmp_path)
    base = asyncio.run(run.evaluate("base"))
    assert base.validation is not None
    samples = Path(base.validation.samples.uri.removeprefix("file://"))
    samples.write_text(samples.read_text() + "\n")
    with pytest.raises(ValueError, match="changed"):
        asyncio.run(run.evaluate("base"))
    (run.root / "protocol.json").write_text("{}")
    with pytest.raises(ValueError, match="changed"):
        _ = run.spec


def test_later_stages_preserve_existing_source_archives(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    policy = source / "policy.py"
    policy.write_text("version = 1\n")
    git_repository(source)
    run = problem_run(tmp_path, TextConfig(source=str(source)))

    async def execute() -> None:
        await run.evaluate("base")
        old_archives = {
            path: path.read_bytes() for path in run.path("base").parent.rglob("*.tar.gz")
        }
        assert old_archives
        policy.write_text("version = 2\n")
        recipe = tmp_path / "recipe.json"
        write_record(recipe, TextRecipe("correct"))
        run.create_candidate("r1", "base", "Exact text with updated policy", recipe)
        await run.evaluate("r1")
        run.select("r1")
        await run.finalize("r1")
        assert all(path.read_bytes() == content for path, content in old_archives.items())

    asyncio.run(execute())


def test_failed_preparation_keeps_a_queryable_record_and_is_not_replayed(tmp_path: Path) -> None:
    run = problem_run(tmp_path)

    async def execute() -> None:
        await run.evaluate("base")
        recipe = tmp_path / "recipe.json"
        write_record(recipe, TextRecipe("invalid-generator"))
        proposed = run.create_candidate("broken", "base", "Test generator failure", recipe)
        assert proposed.status == CandidateStatus.PREPARED and proposed.plan is None
        with pytest.raises(ValueError, match="Generator failed"):
            await run.evaluate("broken")
        failed = run.candidate("broken")
        assert failed.status == CandidateStatus.FAILED and failed.plan is None
        assert run.feedback("broken")[0].error == "Generator failed before producing a candidate"
        with pytest.raises(ValueError, match="use a new candidate ID"):
            await run.evaluate("broken")
        assert run.candidate("broken").error == failed.error
        assert len(list((run.root / "execution").rglob("job.json"))) == 1

    asyncio.run(execute())


def test_interrupted_evaluation_requires_explicit_attempt_without_duplicate_execution(
    tmp_path: Path,
) -> None:
    gate = tmp_path / "gate"
    gate.touch()
    run = problem_run(tmp_path, TextConfig(str(gate)))

    async def execute() -> None:
        operation = asyncio.create_task(run.evaluate("base"))
        started = run.jobs.directory(run.run_id, "base-valid-1") / "started"
        await eventually(started.exists)
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert run.candidate("base").status == CandidateStatus.INTERRUPTED
        with pytest.raises(JobExecutionError):
            await run.evaluate("base")
        assert len(list((run.root / "execution").rglob("job.json"))) == 1
        gate.unlink()
        result = await run.evaluate("base", retry=True)
        assert result.status == CandidateStatus.COMPLETED and result.validation_attempt == 2
        assert run.jobs.detail(run.run_id, "base-valid-1").state == "cancelled"

    asyncio.run(execute())


def test_recovery_feedback_waits_for_reused_execution_instead_of_reporting_old_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("areal_rsi.infra.coordination.runtime.CodexClient", FakeCodex)
    FakeCodex.instances.clear()
    FakeCodex.threads.clear()
    source = tmp_path / "source"
    source.mkdir()
    policy = source / "policy.py"
    policy.write_text("value = 1\n")
    git_repository(source)
    gate = tmp_path / "gate"

    async def execute() -> None:
        system = SessionSystem(tmp_path / "system")
        config = write_record(tmp_path / "config.json", TextConfig(str(gate), source=str(source)))
        run_id = await system.create(
            SessionConfig(str(tmp_path), auto_learn=False),
            problem=ProblemBinding("test_problems:TextProblem", config),
        )
        try:
            await system.prepare_problem(run_id)
            await system.enter_rsi(run_id, "Recover the text task")
            await eventually(lambda: bool(system.detail(run_id).active_turn_id))
            client = FakeCodex.instances[-1]
            assert (
                await client.tool(
                    12,
                    "rsi_set_task",
                    dict(objective="Text", instructions="Recover", acceptance=["Exact answer"]),
                )
            )["success"]
            assert (await client.tool(13, "rsi_start_goal", {}))["success"]
            await client.finish()
            await eventually(lambda: system.detail(run_id).phase == SessionPhase.LEADER)
            await eventually(lambda: bool(system.detail(run_id).active_turn_id))
            recipe = tmp_path / "recipe.json"
            write_record(recipe, TextRecipe("correct"))
            gate.touch()
            await system.submit_candidate(
                run_id, CandidateProposal("r1", "base", "Exact", str(recipe))
            )
            problem = system._problem(run_id)
            started = problem.jobs.directory(run_id, "r1-valid-1") / "started"
            await eventually(started.exists)
            policy.write_text("value = 2\n")
            gate.unlink()
            operation = system.live[run_id].operation
            assert operation is not None
            with pytest.raises(ValueError, match="Source changed"):
                await operation
            assert system.candidate_status(run_id, "r1")[0].status == CandidateStatus.FAILED

            policy.write_text("value = 1\n")
            feedback = await system.evaluate_candidate(run_id, "r1")
            assert feedback.status == CandidateStatus.PREPARED and feedback.error == ""
            operation = system.live[run_id].operation
            assert operation is not None
            result = await operation
            assert result.validation is not None and result.validation.report.score == 1
            assert result.validation_attempt == 1
            assert len(list((problem.root / "execution").rglob("job.json"))) == 2
        finally:
            await system.close()

    asyncio.run(execute())


def test_bound_tools_authorization_completion_and_async_feedback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("areal_rsi.infra.coordination.runtime.CodexClient", FakeCodex)
    FakeCodex.instances.clear()
    FakeCodex.threads.clear()

    async def execute() -> None:
        system = SessionSystem(tmp_path / "system")
        config = write_record(tmp_path / "config.json", TextConfig())
        run_id = await system.create(
            SessionConfig(str(tmp_path), auto_learn=False),
            problem=ProblemBinding("test_problems:TextProblem", config),
        )
        try:
            await system.prepare_problem(run_id)
            await system.enter_rsi(run_id, "Improve the text")
            await eventually(lambda: bool(system.detail(run_id).active_turn_id))
            client = FakeCodex.instances[-1]
            rejected = await client.tool(11, "rsi_candidate_status", {})
            assert not rejected["success"]
            await client.tool(
                12,
                "rsi_set_task",
                dict(objective="Text", instructions="Keep context", acceptance=["Exact answer"]),
            )
            await client.tool(13, "rsi_start_goal", {})
            await client.finish()
            await eventually(lambda: system.detail(run_id).phase == SessionPhase.LEADER)
            await eventually(lambda: bool(system.detail(run_id).active_turn_id))
            rejected = await client.tool(
                14,
                "rsi_update_goal",
                dict(status="completed", summary="Claim", evidence=["text evidence only"]),
            )
            assert not rejected["success"]
            recipe = tmp_path / "recipe.json"
            write_record(recipe, TextRecipe("correct"))
            proposal = CandidateProposal("r1", "base", "Write the exact text", str(recipe))
            response = await client.tool(15, "rsi_submit_candidate", encode_record(proposal))
            assert response["success"]
            assert (
                await client.tool(15, "rsi_submit_candidate", encode_record(proposal))
            ) == response
            operation = system.live[run_id].operation
            assert operation is not None
            await operation
            assert system.candidate_status(run_id, "r1")[0].score == 1
            assert (await client.tool(16, "rsi_select_candidate", {"candidate_id": "r1"}))[
                "success"
            ]
            assert (
                await client.tool(
                    17,
                    "rsi_update_goal",
                    dict(
                        status="completed",
                        summary="Verified",
                        evidence=["Validated and selected r1"],
                    ),
                )
            )["success"]
            await client.finish()
            await eventually(lambda: system.detail(run_id).status == SessionStatus.COMPLETED)
            final = await system.finalize_problem(run_id, "r1")
            assert final.target_met
        finally:
            await system.close()

    asyncio.run(execute())
