"""Research lifecycle on actual CPU artifacts; no simulated GPU training scores."""

import asyncio
import sys
import threading
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from areal_rsi.api.execution import JobSpec
from areal_rsi.api.records import (
    ArtifactVersion,
    ContentRef,
    HarnessVersion,
    ModelVersion,
    SolutionVersion,
    SourceSnapshot,
)
from areal_rsi.api.research import (
    CandidateContext,
    CandidatePlan,
    CandidateStatus,
    EvaluationContext,
    EvaluationPlan,
    ProblemBinding,
    ProblemSpec,
    SourceTree,
)
from areal_rsi.infra.coordination import ProblemRun, pin_problem
from areal_rsi.infra.storage.artifacts import (
    file_ref,
    local_path,
    snapshot_directory,
    write_record,
)


@dataclass(frozen=True)
class TextInput:
    text: str = "wrong"


class TextProblem:
    config_type = recipe_type = TextInput

    def __init__(self, config: TextInput):
        self.config = config

    def prepare(self, root: Path) -> ProblemSpec:
        model = root / "model"
        model.mkdir()
        (model / "config.json").write_text("{}")
        manifest = snapshot_directory(model, root / "model.json")
        answer = root / "base.txt"
        answer.write_text(self.config.text)
        ref = file_ref(answer)
        baseline = SolutionVersion(
            "base",
            ModelVersion("model", "unused", ContentRef(model.as_uri(), manifest.digest), ""),
            HarnessVersion("harness", "text", ref, ""),
            "",
            (ArtifactVersion("base", "answer", ref, ""),),
        )
        return ProblemSpec("text", "Write correct", baseline, manifest)

    def build_candidate(self, context: CandidateContext, recipe: TextInput) -> CandidatePlan:
        answer = Path(context.root) / "candidates" / context.candidate_id / "answer.txt"
        answer.write_text(recipe.text)
        ref = file_ref(answer)
        return CandidatePlan(
            replace(
                context.parent,
                solution_id=context.candidate_id,
                parent_solution_id=context.parent.solution_id,
                artifacts=(ArtifactVersion(context.candidate_id, "answer", ref, ""),),
            ),
            (ref,),
        )

    def build_evaluation(self, context: EvaluationContext) -> EvaluationPlan:
        ref = context.solution.artifacts[0].content
        script = """
import json, sys
from pathlib import Path
answer = Path(sys.argv[1]).read_text()
passed = answer == "correct"
sample = dict(task_id="question", output=answer, score=float(passed), passed=passed,
              events=[dict(kind="generation", content=answer)])
Path(sys.argv[2], "samples.jsonl").write_text(json.dumps(sample) + "\\n")
"""
        job = JobSpec((sys.executable, "-c", script, str(local_path(ref)), context.output))
        return EvaluationPlan(job, ("question",), (ref,))


@pytest.fixture
def research(tmp_path: Path) -> ProblemRun:
    config = write_record(tmp_path / "config.json", TextInput())
    root = tmp_path / "problem"
    binding = pin_problem(ProblemBinding("test_research:TextProblem", config), root)
    run = ProblemRun("run-test", root, binding)
    run.prepare()
    return run


def test_research_reopen_cache_integrity_and_finalization(research: ProblemRun) -> None:
    async def execute() -> None:
        await research.evaluate("base")
        recipe = research.root / "recipe.json"
        write_record(recipe, TextInput("correct"))
        research.create_candidate("r1", "base", "Exact text", recipe)
        candidate = await research.evaluate("r1")
        assert candidate.validation and candidate.validation.report.score == 1
        research.select("r1")
        with pytest.raises(ValueError, match="immutable"):
            research.select("other")
        reopened = ProblemRun(research.run_id, research.root, research.binding)
        final = await reopened.finalize("r1")
        assert final.improvement == 1 and final.target_met
        assert await reopened.finalize("r1") == final
        assert len(list(research.root.rglob("job.json"))) == 4
        samples = local_path(final.candidate.samples)
        samples.write_text(samples.read_text() + "\n")
        with pytest.raises(ValueError, match="changed"):
            await reopened.finalize("r1")

    asyncio.run(execute())


def test_cancel_waits_for_snapshot_without_blocking_queries(
    research: ProblemRun,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started, release = threading.Event(), threading.Event()
    snapshot = research._snapshot

    def slow_snapshot(trees: tuple[SourceTree, ...], directory: Path) -> tuple[SourceSnapshot, ...]:
        started.set()
        assert release.wait(5), "Snapshot blocked the event loop"
        return snapshot(trees, directory)

    monkeypatch.setattr(research, "_snapshot", slow_snapshot)

    async def execute() -> None:
        operation = asyncio.create_task(research.evaluate("base"))
        try:
            async with asyncio.timeout(3):
                while not started.is_set():
                    await asyncio.sleep(0.01)
            assert research.feedback("base")[0].candidate_id == "base"
            operation.cancel()
            await asyncio.sleep(0)
            assert not operation.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await operation
            assert research.candidate("base").status == CandidateStatus.INTERRUPTED
            assert not research.lock.locked()
        finally:
            release.set()
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)

    asyncio.run(execute())
