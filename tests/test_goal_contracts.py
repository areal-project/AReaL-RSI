"""Offline Goal/Skill/Model contracts; these scores are never demo evidence."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from areal_rsi.api.base import (
    BenchmarkSplit,
    ContentRef,
    EvaluationReport,
    EvolutionTarget,
    ModelChange,
    ModelVersion,
)
from areal_rsi.api.goal import (
    BudgetSpec,
    GoalSpec,
    GoalState,
    MetricRequirement,
    accepts,
    comparison_metrics,
)
from areal_rsi.api.training import TrainingJob, TrainingRequest, TrainingState
from areal_rsi.base.storage import FileContentStore, decode, encode, now
from areal_rsi.goals import GoalService
from areal_rsi.modules.skills import SkillLibrary, SkillLoader


def request() -> dict[str, Any]:
    return {
        "description": "Improve arithmetic",
        "target": "harness",
        "acceptance": {
            "metrics": [{"metric": "accuracy", "threshold": 0.9}],
            "required_checks": ["structured"],
            "verifier_version": "v1",
        },
        "budget": {"max_iterations": 3, "max_seconds": 10, "max_retries": 1},
        "allowed_paths": ["prompt.md"],
        "invariants": ["fixed model"],
    }


def test_contract_roundtrip_and_fail_closed() -> None:
    spec = decode(GoalSpec, request())
    assert spec.target == EvolutionTarget.HARNESS
    assert decode(GoalSpec, encode(spec)) == spec
    assert not accepts(spec.acceptance, {"accuracy": 1.0})
    assert not accepts(spec.acceptance, {"accuracy": float("nan"), "structured": 1.0})
    assert accepts(spec.acceptance, {"accuracy": 0.9, "structured": 1.0})
    assert not accepts(spec.acceptance, {"accuracy": 0.89, "structured": 1.0})
    spec.acceptance.metrics += (MetricRequirement("gain_accuracy", 0.1),)
    assert accepts(spec.acceptance, {"accuracy": 0.9, "structured": 1.0})
    assert not accepts(spec.acceptance, {"accuracy": 0.9, "structured": 1.0}, final=True)


@pytest.mark.parametrize(
    "direction,threshold,value,expected",
    [
        ("gte", 0.1, 1.0 - 0.9, True),
        ("gte", 0.1, 0.09999, False),
        ("lte", 0.3, 0.1 + 0.2, True),
        ("lte", 0.3, 0.30001, False),
    ],
)
def test_acceptance_numeric_boundary_does_not_relax_real_deficits(
    direction: str, threshold: float, value: float, expected: bool
) -> None:
    spec = decode(GoalSpec, request()).acceptance
    spec.metrics = (MetricRequirement("gain_score", threshold, direction),)
    assert accepts(spec, {"gain_score": value, "structured": 1.0}, final=True) is expected


def test_gain_requires_both_reports_and_includes_score() -> None:
    before = EvaluationReport("base", BenchmarkSplit.FINAL, False, 0.9, {}, now())
    after = EvaluationReport("candidate", BenchmarkSplit.FINAL, True, 1.0, {"accuracy": 1.0}, now())
    metrics = comparison_metrics(before, after)
    assert "gain_accuracy" not in metrics
    assert metrics["gain_score"] == 1.0 - 0.9
    spec = decode(GoalSpec, request()).acceptance
    spec.metrics = (MetricRequirement("gain_accuracy", 0.1),)
    spec.required_checks = ()
    assert not accepts(spec, metrics, final=True)


def test_nested_answers_merge_and_missing_fields_remain_questions(tmp_path: Path) -> None:
    service = GoalService(tmp_path)
    value = request()
    value["acceptance"] = {"metrics": [{"metric": "accuracy"}]}
    value["budget"] = {"max_iterations": 3}
    goal = service.draft(value)
    assert goal.state == GoalState.NEEDS_INPUT
    assert any("acceptance.verifier_version" in q for q in goal.questions)
    assert any("metrics[0].threshold" in q for q in goal.questions)
    assert any("budget.max_seconds" in q for q in goal.questions)
    goal = service.answer(
        goal.goal_id,
        {
            "acceptance": {
                "metrics": [{"metric": "accuracy", "threshold": 0.9}],
                "verifier_version": "v1",
            }
        },
    )
    assert goal.state == GoalState.NEEDS_INPUT
    goal = service.answer(goal.goal_id, {"budget": {"max_seconds": 10}})
    assert goal.state == GoalState.READY and goal.spec
    assert goal.spec.budget.max_iterations == 3
    assert goal.spec.acceptance.metrics[0].threshold == 0.9
    value["acceptance"] = {"metrics": [], "verifier_version": "v1"}
    assert any("acceptance.metrics" in q for q in service.draft(value).questions)


def test_seeded_browser_final_inputs_are_fresh_and_default_is_compatible() -> None:
    from areal_rsi.modules.browser import BrowserBenchmark

    async def scenario() -> None:
        original = await BrowserBenchmark("task-web-v1").list_tasks(BenchmarkSplit.FINAL)
        fresh = await BrowserBenchmark("task-web-v1", seed=42).list_tasks(BenchmarkSplit.FINAL)
        assert original[0].task_id == "task-web-v1-final-1280"
        assert {json.dumps(task.input, sort_keys=True) for task in original}.isdisjoint(
            {json.dumps(task.input, sort_keys=True) for task in fresh}
        )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "iterations,seconds,retries",
    [(0, 1, 0), (1, 0, 0), (1, 1, -1), (True, 1, 0), (1, float("inf"), 0)],
)
def test_invalid_budgets(iterations: int, seconds: float, retries: int) -> None:
    with pytest.raises(ValueError):
        BudgetSpec(iterations, seconds, retries)


def test_revision_preserves_history_and_never_runs_incomplete(tmp_path: Path) -> None:
    goals = GoalService(tmp_path)
    draft = goals.draft({"description": "Improve"})
    assert draft.state == GoalState.NEEDS_INPUT
    with pytest.raises(ValueError, match="confirmed"):
        goals.launch(draft.goal_id)
    revised = goals.answer(draft.goal_id, request())
    assert revised.revision == 2 and revised.state == GoalState.READY
    assert goals.get(draft.goal_id) == revised
    assert (tmp_path / "goal-revisions" / f"{draft.goal_id}-r1.json").exists()
    with pytest.raises(ValueError, match="Credentials"):
        goals.answer(draft.goal_id, {"assets": {"api_key": "secret"}})
    assert goals.get(draft.goal_id).revision == 2


def test_noninteractive_cli_is_ndjson_and_does_not_wait(tmp_path: Path) -> None:
    env = {key: value for key, value in os.environ.items() if not key.startswith("RSI_")}
    env.update(HOME=str(tmp_path), RSI_DATA_ROOT=str(tmp_path / "data"))
    result = subprocess.run(
        [sys.executable, "-m", "areal_rsi", "exec", "Improve", "--yes", "--json"],
        env=env,
        input="",
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 3, result.stderr
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert events[-1]["type"] == "needs_input" and events[-1]["schema_version"] == 1
    assert events[-1]["run_id"] is None


def make_skill(path: Path) -> None:
    path.mkdir()
    (path / "SKILL.md").write_text(
        "---\nname: local-state\nversion: 1.0.0\ndescription: Test method\n"
        "scope: Static web apps\ndependencies: [python]\n---\n"
        "Read references/checklist.md.\n"
    )
    (path / "references").mkdir()
    (path / "references/checklist.md").write_text("Check persistence after reload.\n")
    (path / "scripts").mkdir()
    (path / "scripts/check.py").write_text("print('resource-ok')\n")
    (path / "scripts/validate.py").write_text(
        "from pathlib import Path\n"
        "assert Path('package/references/checklist.md').read_text().strip()\n"
        "print('scenario-ok')\n"
    )


def test_skill_snapshot_survives_source_mutation_and_library_lifecycle(tmp_path: Path) -> None:
    async def scenario() -> None:
        content = FileContentStore(tmp_path / "content")
        source = tmp_path / "source"
        make_skill(source)
        loader = SkillLoader(content)
        skill = await loader.freeze(source)
        library = SkillLibrary(tmp_path / "library", content)
        record = await library.add(
            skill, {"goal_id": "goal_fixture", "final_evidence_access": False}
        )
        assert record["state"] == "candidate"
        with pytest.raises(ValueError):
            library.activate(skill.skill_id, skill.version)
        (source / "references/checklist.md").write_text("mutated")
        (source / "SKILL.md").unlink()
        workspace = tmp_path / "workspace"
        directory, prompt = await loader.materialize(skill, workspace)
        assert (
            directory / "references/checklist.md"
        ).read_text() == "Check persistence after reload.\n"
        assert str(directory.relative_to(workspace)) in await content.read_text(prompt)
        assert (directory / "scripts/check.py").stat().st_mode & 0o222 == 0
        validated = await library.validate(skill.skill_id, skill.version)
        assert validated["state"] == "validated"
        assert not validated["validation"]["cross_task_benefit_proven"]
        assert library.activate(skill.skill_id, skill.version)["state"] == "active"

    asyncio.run(scenario())


def test_skill_path_and_secret_boundaries(tmp_path: Path) -> None:
    async def scenario() -> None:
        source = tmp_path / "source"
        make_skill(source)
        loader = SkillLoader(FileContentStore(tmp_path / "content"))
        (source / "references/escape").symlink_to(tmp_path / "private")
        with pytest.raises(ValueError, match="symlink"):
            await loader.freeze(source)
        (source / "references/escape").unlink()
        (source / "references/leak").write_text("sk-" + "x" * 30)
        with pytest.raises(ValueError, match="Credential"):
            await loader.freeze(source)

    asyncio.run(scenario())


def test_skill_capture_recovers_explicit_committed_files_without_extra_workspace_data(
    tmp_path: Path,
) -> None:
    from areal_rsi.skill_capture import package_proposal

    workspace = tmp_path / "workspace"
    make_skill(workspace)
    (workspace / "development.json").write_text('"private development input"')
    package = package_proposal(
        workspace,
        {"workspace_files": ["SKILL.md", "references/checklist.md", "scripts/validate.py"]},
    )
    assert (package / "SKILL.md").read_bytes() == (workspace / "SKILL.md").read_bytes()
    assert not (package / "development.json").exists()
    outside = tmp_path / "outside"
    outside.write_text("outside")
    (workspace / "escape").symlink_to(outside)
    for path in ("escape", "../outside"):
        with pytest.raises(ValueError):
            package_proposal(workspace, {"workspace_files": ["SKILL.md", path]})


class SimulatedTraining:
    """Explicit simulated backend for protocol testing; never used by LocalApp."""

    def __init__(self) -> None:
        self.jobs: dict[str, TrainingJob] = {}

    async def submit(self, request: TrainingRequest) -> TrainingJob:
        if request.execution_key in self.jobs:
            job = self.jobs[request.execution_key]
            if job.request != request:
                raise ValueError("Idempotency key conflict")
            return job
        job = TrainingJob(request.execution_key, request, TrainingState.SUBMITTED)
        self.jobs[job.job_id] = job
        return job

    async def status(self, job_id: str) -> TrainingJob:
        return self.jobs[job_id]

    async def cancel(self, job_id: str) -> TrainingJob:
        self.jobs[job_id].state = TrainingState.CANCELLED
        return self.jobs[job_id]

    async def resume(self, job_id: str, *, checkpoint: ContentRef | None = None) -> TrainingJob:
        job = self.jobs[job_id]
        if checkpoint is not None and checkpoint != job.checkpoint:
            raise ValueError("Checkpoint belongs to a different training job")
        job.state = TrainingState.RUNNING
        return job


def test_model_lifecycle_and_parent_checkpoint_contract() -> None:
    async def scenario() -> None:
        ref = ContentRef("test://weights", "simulation-only")
        model = ModelVersion("model_parent", "mock", ref, now())
        change = ModelChange(ContentRef("test://dataset", "sha256:data"), {"lr": 0.01}, "test")
        request = TrainingRequest("train_1", model, change, {"gpu": 1})
        backend = SimulatedTraining()
        job = await backend.submit(request)
        assert await backend.submit(request) is job
        assert (await backend.status(job.job_id)).state == TrainingState.SUBMITTED
        assert (await backend.cancel(job.job_id)).state == TrainingState.CANCELLED
        assert (await backend.resume(job.job_id)).state == TrainingState.RUNNING
        with pytest.raises(ValueError, match="real checkpoint"):
            TrainingJob("invalid", request, TrainingState.COMPLETED)
        job.checkpoint = ContentRef("test://simulated-checkpoint", "simulation-only")
        job.state = TrainingState.COMPLETED
        assert job.request.parent.version_id == model.version_id
        assert decode(TrainingJob, encode(job)) == job
        with pytest.raises(ValueError, match="different"):
            await backend.resume(job.job_id, checkpoint=ref)
        assert (
            await backend.resume(job.job_id, checkpoint=job.checkpoint)
        ).checkpoint == job.checkpoint

    asyncio.run(scenario())


def test_same_name_skill_updates_preserve_old_package(tmp_path: Path) -> None:
    async def scenario() -> None:
        content = FileContentStore(tmp_path / "content")
        source = tmp_path / "source"
        make_skill(source)
        loader = SkillLoader(content)
        library = SkillLibrary(tmp_path / "library", content)
        original = await loader.freeze(source)
        await library.add(original, {"goal_id": "first"})
        (source / "references/checklist.md").write_text("Updated transferable method.\n")
        updated = await library.add(await loader.freeze(source), {"goal_id": "second"})
        assert updated["skill"]["version"] == "1.0.1"
        assert library.inspect(original.skill_id, "1.0.0")["skill"] == encode(original)
        assert (
            library.path(original.skill_id, "1.0.0") / "package/references/checklist.md"
        ).read_text() == "Check persistence after reload.\n"

    asyncio.run(scenario())


@pytest.mark.parametrize("paths", [None, (), ("/some/skill",)])
def test_optional_skill_selection_roundtrip(paths: tuple[str, ...] | None) -> None:
    spec = decode(GoalSpec, request())
    spec.skill_paths = paths
    assert decode(GoalSpec, encode(spec)).skill_paths == paths


def test_resumed_goal_does_not_present_previous_pause_as_current_outcome(tmp_path: Path) -> None:
    from areal_rsi.base.storage import write_json

    service = GoalService(tmp_path)
    goal = service.draft(request())
    goal.state = GoalState.RUNNING
    service.save(goal)
    write_json(
        tmp_path / "goal-outcomes" / f"{service.key(goal)}.json",
        {"state": "paused", "achieved": False, "elapsed_seconds": 12},
    )
    write_json(tmp_path / "goal-workers" / f"{service.key(goal)}-ledger.json", {"elapsed": 20})
    status = service.status(goal.goal_id)
    assert status["state"] == "running"
    assert status["outcome"] is None
    assert status["previous_outcome"]["state"] == "paused"
    assert status["resource_usage"]["elapsed"] == 20
    assert status["recovery_required"]
