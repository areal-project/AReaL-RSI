"""Typed experiment coordination shared by task adapters and ResearchSystem."""

from __future__ import annotations

import asyncio
import fcntl
import importlib
import json
import re
from dataclasses import is_dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from areal_rsi.api.control import encode_record, record_schema
from areal_rsi.api.interfaces import ResearchProblem
from areal_rsi.api.records import (
    BenchmarkSplit,
    ContentRef,
    EvaluationResult,
    ModelVersion,
    PairedComparison,
    SolutionVersion,
    SourceSnapshot,
)
from areal_rsi.api.research import (
    CandidateContext,
    CandidateFeedback,
    CandidatePlan,
    CandidateRecord,
    CandidateSelection,
    CandidateStatus,
    EvaluationContext,
    EvaluationPlan,
    FinalEvaluation,
    ProblemBinding,
    ProblemSpec,
    SourceTree,
)
from areal_rsi.infra.artifacts import (
    file_ref,
    local_path,
    read_record,
    snapshot_source,
    verify_model,
    write_record,
)
from areal_rsi.infra.coordination.evaluation import (
    EvaluationService,
    paired,
    read_evaluation_samples,
)
from areal_rsi.infra.coordination.policies.selection import improvement, metric, select_candidate
from areal_rsi.infra.execution import JobExecutor
from areal_rsi.infra.training import AReaLBackend
from areal_rsi.infra.trajectories import LocalTrajectoryStore


def _problem_factory(entrypoint: str) -> tuple[Any, Path]:
    """Decode the operator-owned plugin boundary before creating persistent state."""
    module_name, separator, factory_name = entrypoint.partition(":")
    if not separator or not factory_name.isidentifier():
        raise ValueError("Problem entrypoint must be module:Class")
    try:
        module = importlib.import_module(module_name)
        factory = getattr(module, factory_name)
    except (ImportError, AttributeError) as exc:
        raise ValueError(f"Cannot load research problem {entrypoint}") from exc
    if not module.__file__:
        raise ValueError("Problem adapters must have a source file")
    if (
        not isinstance(factory, type)
        or any(
            not is_dataclass(getattr(factory, name, None))
            for name in ("config_type", "recipe_type")
        )
        or any(
            not callable(getattr(factory, name, None))
            for name in ("prepare", "build_candidate", "build_evaluation")
        )
    ):
        raise ValueError(
            "A research problem requires dataclass config/recipe and all three plan methods"
        )
    return factory, Path(module.__file__)


def pin_problem(binding: ProblemBinding, root: Path) -> ProblemBinding:
    factory, source = _problem_factory(binding.entrypoint)
    adapter = file_ref(source)
    if binding.adapter is not None and binding.adapter != adapter:
        raise ValueError("Problem adapter changed")
    config_path = local_path(binding.config)
    read_record(config_path, factory.config_type)
    destination = root / "config.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(config_path.read_bytes())
    return ProblemBinding(binding.entrypoint, file_ref(destination), adapter, binding.objective)


class ProblemRun:
    """One run's candidates and evidence; conversation and process states stay elsewhere."""

    def __init__(self, run_id: str, root: Path, binding: ProblemBinding):
        self.run_id, self.root, self.binding = run_id, root, binding
        if binding.adapter is None:
            raise ValueError("Problem binding has not been pinned")
        local_path(binding.adapter)
        factory, _ = _problem_factory(binding.entrypoint)
        # The plugin is the only dynamic boundary. All persisted state is named records.
        config = read_record(local_path(binding.config), factory.config_type)
        self.problem = cast(ResearchProblem[Any, Any], factory(config))
        self.jobs = JobExecutor(root / "execution")
        self.evaluator = EvaluationService(self.jobs, LocalTrajectoryStore(root / "trajectories"))
        self.lock = asyncio.Lock()

    @property
    def spec(self) -> ProblemSpec:
        freeze = read_record(self.root / "freeze.json", ContentRef)
        spec = read_record(local_path(freeze), ProblemSpec)
        local_path(self.binding.config)
        assert self.binding.adapter is not None
        for ref in (self.binding.adapter, *spec.locked_files, *spec.contract.conditions):
            local_path(ref)
        return spec

    def path(self, candidate_id: str) -> Path:
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", candidate_id):
            raise ValueError("Use a simple 1..64 character candidate ID")
        return self.root / "candidates" / candidate_id / "candidate.json"

    def candidate(self, candidate_id: str) -> CandidateRecord:
        record = read_record(self.path(candidate_id), CandidateRecord)
        if record.candidate_id != candidate_id:
            raise ValueError("Candidate identity differs from its record path")
        return record

    def candidates(self) -> list[CandidateRecord]:
        return [
            self.candidate(path.parent.name)
            for path in sorted((self.root / "candidates").glob("*/candidate.json"))
        ]

    def context(self) -> str:
        """Render frozen instructions without hashing large evaluation inputs every turn."""
        freeze = read_record(self.root / "freeze.json", ContentRef)
        spec = read_record(local_path(freeze), ProblemSpec)
        return (
            "\nBound research problem:\n"
            + spec.instructions
            + "\nCandidate execution is asynchronous. After a pending handle or status "
            "(prepared/preparing/training/evaluating), finish this turn with a short status. "
            "The host automatically resumes you with completion or failure feedback. "
            "Ending that response does not complete or pause the research goal; do not poll "
            "candidate status repeatedly while the same operation is running.\n"
            + "\nRecipe schema:\n"
            + json.dumps(record_schema(self.problem.recipe_type))
            + "\nCandidate feedback:\n"
            + json.dumps([encode_record(item) for item in self.feedback()])
        )

    def solution(self, candidate_id: str) -> SolutionVersion:
        record = self.candidate(candidate_id)
        if record.solution is None or record.plan is None:
            raise ValueError("Candidate has no validated solution")
        manifest = record.training.manifest if record.training else self.spec.model_manifest
        verify_model(record.solution, manifest)
        for ref in record.plan.inputs:
            local_path(ref)
        return record.solution

    def _snapshot(
        self, trees: tuple[SourceTree, ...], directory: Path
    ) -> tuple[SourceSnapshot, ...]:
        return tuple(
            snapshot_source(Path(tree.root), directory / f"source-{i}.json", paths=tree.paths)
            for i, tree in enumerate(trees)
        )

    def prepare(self) -> ProblemSpec:
        if (self.root / "freeze.json").exists():
            return self.spec
        self.root.mkdir(parents=True, exist_ok=True)
        spec = self.problem.prepare(self.root)
        if spec.baseline.solution_id != "base":
            raise ValueError("The baseline solution ID must be base")
        verify_model(spec.baseline, spec.model_manifest)
        for ref in (*spec.locked_files, *spec.contract.conditions):
            local_path(ref)
        spec_ref = write_record(self.root / "protocol.json", spec)
        base = CandidateRecord(
            "base",
            None,
            "Frozen baseline",
            None,
            CandidatePlan(spec.baseline),
            self._snapshot(spec.sources, self.root / "baseline-source"),
            solution=spec.baseline,
        )
        write_record(self.path("base"), base)
        write_record(self.root / "freeze.json", spec_ref)
        return spec

    def create_candidate(
        self, candidate_id: str, parent_id: str, hypothesis: str, recipe_path: Path
    ) -> CandidateRecord:
        if candidate_id == "base" or (self.root / "selection.json").exists():
            raise ValueError("Invalid candidate ID or selection already frozen")
        if not hypothesis.strip():
            raise ValueError("A candidate requires a research hypothesis")
        if self.candidate("base").validation is None:
            raise ValueError("Evaluate the baseline before proposing candidates")
        if self.candidate(parent_id).solution is None:
            raise ValueError("Parent has no validated solution")
        recipe = read_record(recipe_path, self.problem.recipe_type)
        path = self.path(candidate_id)
        if path.exists():
            saved = self.candidate(candidate_id)
            if (
                saved.parent_id != parent_id
                or saved.hypothesis != hypothesis
                or saved.recipe is None
                or read_record(local_path(saved.recipe), self.problem.recipe_type) != recipe
            ):
                raise ValueError("Candidate ID already has a different proposal")
            return saved
        path.parent.mkdir(parents=True, exist_ok=False)
        recipe_ref = write_record(path.parent / "recipe.json", recipe)
        record = CandidateRecord(candidate_id, parent_id, hypothesis, recipe_ref, None)
        write_record(path, record)
        return record

    def _build_candidate(self, record: CandidateRecord) -> None:
        record.status = CandidateStatus.PREPARING
        path = self.path(record.candidate_id)
        write_record(path, record)
        spec = self.spec
        assert record.parent_id is not None and record.recipe is not None
        parent = self.solution(record.parent_id)
        recipe = read_record(local_path(record.recipe), self.problem.recipe_type)
        output = self.jobs.directory(self.run_id, record.candidate_id + "-train")
        record.sources = self._snapshot(spec.sources, path.parent / "source")
        write_record(path, record)
        plan = self.problem.build_candidate(
            CandidateContext(
                self.run_id, str(self.root), record.candidate_id, parent, spec, str(output)
            ),
            recipe,
        )
        if plan.solution.solution_id != record.candidate_id:
            raise ValueError("Candidate plan has a different solution ID")
        if plan.solution.parent_solution_id != parent.solution_id:
            raise ValueError("Candidate plan has a different parent solution")
        for ref in plan.inputs:
            local_path(ref)
        record.plan = plan
        self._check_source(record, "prepared")
        record.status = CandidateStatus.PREPARED
        write_record(path, record)

    async def run_candidate(self, candidate_id: str) -> CandidateRecord:
        async with self.lock:
            with (self.root / "execution.lock").open("a") as lease:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                record = self.candidate(candidate_id)
                if record.plan is None and record.status != CandidateStatus.PREPARED:
                    raise ValueError(
                        "Candidate preparation failed or was interrupted; use a new candidate ID"
                    )
                try:
                    if record.plan is None:
                        preparing = asyncio.create_task(
                            asyncio.to_thread(self._build_candidate, record)
                        )
                        try:
                            await asyncio.shield(preparing)
                        except asyncio.CancelledError:
                            # Finish bounded local preparation before publishing interruption,
                            # so a cancelled thread cannot overwrite the durable terminal record.
                            await asyncio.gather(preparing, return_exceptions=True)
                            raise
                    plan = record.plan
                    assert plan is not None
                    if record.recipe:
                        local_path(record.recipe)
                    for ref in plan.inputs:
                        local_path(ref)
                    if record.solution is None:
                        self._check_source(record, "before")
                        if plan.job is not None:
                            assert plan.runtime is not None
                            record.status = CandidateStatus.TRAINING
                            write_record(self.path(candidate_id), record)
                            training = await AReaLBackend(plan.runtime, self.jobs).train(
                                self.run_id, candidate_id + "-train", plan.job
                            )
                            record.training = training
                            self._check_source(record, "trained")
                            model = plan.solution.model
                            created = datetime.now(UTC).isoformat()
                            record.solution = replace(
                                plan.solution,
                                model=ModelVersion(
                                    "model-" + training.checkpoint.digest[:20],
                                    candidate_id,
                                    training.checkpoint,
                                    created,
                                    model.version_id,
                                ),
                                created_at=created,
                            )
                        else:
                            record.solution = plan.solution
                        write_record(self.path(candidate_id), record)
                    await self._evaluate(record, BenchmarkSplit.VALIDATE)
                    record.status, record.error = CandidateStatus.COMPLETED, ""
                except BaseException as exc:
                    record = self.candidate(candidate_id)
                    record.status = (
                        CandidateStatus.INTERRUPTED
                        if isinstance(exc, asyncio.CancelledError)
                        else CandidateStatus.FAILED
                    )
                    record.error = str(exc) or type(exc).__name__
                    write_record(self.path(candidate_id), record)
                    raise
                write_record(self.path(candidate_id), record)
                return record

    def _check_source(
        self,
        record: CandidateRecord,
        phase: str,
        expected: tuple[SourceSnapshot, ...] | None = None,
    ) -> None:
        spec = self.spec
        current = self._snapshot(
            spec.sources, self.path(record.candidate_id).parent / (phase + "-source") / uuid4().hex
        )
        if tuple(item.content.digest for item in current) != tuple(
            item.content.digest for item in (record.sources if expected is None else expected)
        ):
            raise ValueError("Source changed during a candidate stage; preserve this attempt")

    async def _evaluate(self, record: CandidateRecord, split: BenchmarkSplit) -> EvaluationResult:
        candidate_id = record.candidate_id
        spec = self.spec
        solution = self.solution(candidate_id)
        attempt = record.test_attempt if split == BenchmarkSplit.TEST else record.validation_attempt
        stem = f"{candidate_id}-{'test' if split == BenchmarkSplit.TEST else 'valid'}-{attempt}"
        plan_path = self.path(candidate_id).parent / f"{stem}-plan.json"
        plan_ref = record.test_plan if split == BenchmarkSplit.TEST else record.validation_plan
        if plan_ref is not None:
            plan = read_record(local_path(plan_ref), EvaluationPlan)
        else:
            plan = self.problem.build_evaluation(
                EvaluationContext(
                    self.run_id,
                    str(self.root),
                    spec,
                    solution,
                    split,
                    str(self.jobs.directory(self.run_id, stem)),
                )
            )
            plan = replace(
                plan,
                sources=self._snapshot(
                    spec.sources, self.path(candidate_id).parent / (stem + "-source")
                ),
            )
            plan_ref = write_record(plan_path, plan)
            if split == BenchmarkSplit.TEST:
                record.test_plan = plan_ref
            else:
                record.validation_plan = plan_ref
            write_record(self.path(candidate_id), record)
        for ref in plan.inputs:
            local_path(ref)
        saved = record.test if split == BenchmarkSplit.TEST else record.validation
        if saved is not None:
            read_evaluation_samples(
                saved, task_ids=plan.task_ids, solution_id=solution.solution_id, split=split
            )
            return saved
        if split == BenchmarkSplit.VALIDATE:
            record.status = CandidateStatus.EVALUATING
        write_record(self.path(candidate_id), record)
        self._check_source(record, stem + "-before", plan.sources)
        result = await self.evaluator.evaluate(
            self.run_id,
            stem,
            plan.job,
            task_ids=plan.task_ids,
            solution_id=solution.solution_id,
            split=split,
        )
        self._check_source(record, stem + "-after", plan.sources)
        if split == BenchmarkSplit.TEST:
            record.test = result
        else:
            record.validation = result
        write_record(self.path(candidate_id), record)
        return result

    async def evaluate(self, candidate_id: str, *, retry: bool = False) -> CandidateRecord:
        if (self.root / "selection.json").exists():
            raise ValueError("Selection is frozen; further tuning requires a new run")
        if retry:
            record = self.candidate(candidate_id)
            if record.validation is not None:
                raise ValueError("A completed evaluation cannot be retried")
            record.validation_attempt += 1
            record.validation_plan = None
            write_record(self.path(candidate_id), record)
        return await self.run_candidate(candidate_id)

    def select(self, candidate_id: str) -> CandidateSelection:
        spec = self.spec
        path = self.root / "selection.json"
        if path.exists():
            selection = read_record(path, CandidateSelection)
            if selection.candidate_id != candidate_id:
                raise ValueError("Candidate selection is immutable")
            local_path(selection.protocol)
            frozen_validation = read_record(local_path(selection.validation), EvaluationResult)
            if (
                selection.solution != self.solution(candidate_id)
                or self.candidate(candidate_id).validation != frozen_validation
            ):
                raise ValueError("Selected candidate evidence changed")
            read_evaluation_samples(
                frozen_validation,
                solution_id=selection.solution.solution_id,
                split=BenchmarkSplit.VALIDATE,
            )
            return selection
        candidates = self.candidates()
        for candidate in candidates:
            if candidate.validation is not None:
                if candidate.validation_plan is None:
                    raise ValueError("Candidate has no frozen evaluation plan")
                plan = read_record(local_path(candidate.validation_plan), EvaluationPlan)
                for ref in plan.inputs:
                    local_path(ref)
                read_evaluation_samples(
                    candidate.validation,
                    task_ids=plan.task_ids,
                    solution_id=self.solution(candidate.candidate_id).solution_id,
                    split=BenchmarkSplit.VALIDATE,
                )
        selected = select_candidate(spec.contract, candidates, candidate_id)
        assert selected.validation is not None
        validation = write_record(
            self.path(candidate_id).parent / "selected-validation.json", selected.validation
        )
        selection = CandidateSelection(
            candidate_id,
            self.solution(candidate_id),
            file_ref(self.root / "protocol.json"),
            validation,
            datetime.now(UTC).isoformat(),
        )
        write_record(path, selection)
        return selection

    def verify_completion(self) -> CandidateSelection:
        selection = read_record(self.root / "selection.json", CandidateSelection)
        return self.select(selection.candidate_id)

    async def finalize(self, candidate_id: str) -> FinalEvaluation:
        async with self.lock:
            with (self.root / "execution.lock").open("a") as lease:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.select(candidate_id)
                baseline = await self._evaluate(self.candidate("base"), BenchmarkSplit.TEST)
                candidate = await self._evaluate(self.candidate(candidate_id), BenchmarkSplit.TEST)
                contract = self.spec.contract
                delta = improvement(
                    contract, metric(contract, baseline), metric(contract, candidate)
                )
                result = FinalEvaluation(
                    file_ref(self.root / "selection.json"),
                    baseline,
                    candidate,
                    delta,
                    contract.target_delta is None or delta >= contract.target_delta,
                    self._paired(candidate_id, baseline, candidate),
                )
                write_record(self.root / "final.json", result)
                (self.root / "report.md").write_text(
                    f"# {self.spec.name}: {self.run_id}\n\n"
                    f"Selected: {candidate_id}\n\n"
                    f"Metric: {contract.metric} ({contract.direction}); "
                    f"baseline {metric(contract, baseline):.6g}, "
                    f"candidate {metric(contract, candidate):.6g}, improvement {delta:+.6g}.\n\n"
                    f"Target met: {result.target_met}.\n\n"
                    "Full frozen selection, evidence, and paired statistics: final.json.\n"
                )
                return result

    def _paired(
        self, candidate_id: str, baseline: EvaluationResult, candidate: EvaluationResult
    ) -> PairedComparison | None:
        record = self.candidate(candidate_id)
        assert record.test_plan is not None
        plan = read_record(local_path(record.test_plan), EvaluationPlan)
        samples = (*read_evaluation_samples(baseline), *read_evaluation_samples(candidate))
        if all(sample.score == float(sample.passed) for sample in samples):
            return paired(baseline, candidate, clusters=plan.clusters)
        return None

    def feedback(self, candidate_id: str = "") -> list[CandidateFeedback]:
        records = [self.candidate(candidate_id)] if candidate_id else self.candidates()
        return [
            CandidateFeedback(
                item.candidate_id,
                item.parent_id,
                item.hypothesis,
                item.status,
                item.validation.report.score if item.validation else None,
                item.validation.report.metrics if item.validation else {},
                item.training.global_step if item.training else None,
                (item.validation.samples,) if item.validation else (),
                item.error,
            )
            for item in records
        ]
