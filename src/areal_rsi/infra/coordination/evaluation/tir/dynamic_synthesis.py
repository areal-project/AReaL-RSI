"""Refresh and calibrate synthetic training data before every RSI round."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import replace
from pathlib import Path

from areal_rsi.api.control import json_bytes, read_record
from areal_rsi.api.records import AgentEvaluationReport, RsiSplit
from areal_rsi.infra.coordination.continuation import (
    ROOT,
    ContinuationPlan,
    RoundResult,
    digest,
    run,
    sources,
)
from areal_rsi.infra.coordination.evaluation.tasks import load_task_set
from areal_rsi.infra.coordination.evaluation.tir.synthesis import (
    DiverseMathFamily,
    SyntheticMathCase,
    freeze_tir_synthetic_probe,
    freeze_tir_synthetic_training,
    select_model_calibrated_cases,
    synthesize_diverse_math_cases,
)
from areal_rsi.infra.storage.files import write_bytes


def fresh_candidates(seed: int, excluded: set[str]) -> tuple[SyntheticMathCase, ...]:
    """Draw 1024 balanced questions, excluding every earlier probe and source task."""
    chosen: list[SyntheticMathCase] = []
    seen = set(excluded)
    counts = {f"{family.value}_{variant}": 0 for family in DiverseMathFamily for variant in (0, 1)}
    for attempt in range(100):
        for item in synthesize_diverse_math_cases(seed=seed + attempt, count=1024):
            if item.case.task_id not in seen and counts[item.recipe] < 64:
                chosen.append(item)
                seen.add(item.case.task_id)
                counts[item.recipe] += 1
        if len(chosen) == 1024:
            return tuple(chosen)
    raise ValueError("Generator cannot supply a fresh balanced candidate batch")


def calibrated_scores(
    probe: Path, reports: tuple[Path, Path], model: str, prompt: Path
) -> tuple[int | None, ...]:
    tasks = load_task_set(probe)
    first, second = (read_record(path, AgentEvaluationReport) for path in reports)
    for path, report in zip(reports, (first, second), strict=True):
        if (
            report.model != model
            or report.split is not RsiSplit.HARNESS_EVOLVE
            or report.task_set_id != tasks.task_set_id
            or tuple(c.task_id for c in report.cases) != tasks.harness_evolve.task_ids
        ):
            raise ValueError("Calibration model or task identity mismatch")
        if (path.parent / "harness/prompt.txt").read_bytes() != prompt.read_bytes():
            raise ValueError("Calibration prompt changed")
        for case in report.cases:
            if (
                case.score not in (0, 1)
                or digest(Path(case.transcript_path)) != case.transcript_digest
            ):
                raise ValueError("Invalid calibration score or transcript")
    if (
        first.backend_model_digest != second.backend_model_digest
        or first.harness_id != second.harness_id
        or first.verifier_digest != second.verifier_digest
        or first.executable_digest != second.executable_digest
    ):
        raise ValueError("Calibration execution changed between repeats")
    return tuple(
        int(a.score + b.score) if a.failure_reason is None and b.failure_reason is None else None
        for a, b in zip(first.cases, second.cases, strict=True)
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--first-round-calibration", type=Path)
    args = parser.parse_args()
    plan = read_record(args.plan, ContinuationPlan)
    if plan.large_model or plan.minimum_rounds != 3 or plan.maximum_rounds != 3:
        raise ValueError("This experiment requires three 1.5B rounds")
    parent = Path(plan.task_set)
    tasks = load_task_set(parent)
    if not (tasks.harness_evolve == tasks.harness_select == tasks.model_train):
        raise ValueError("Harness and model training must share the training set")
    if len(tasks.model_train.task_ids) != 512 or len(tasks.model_dev.task_ids) != 256:
        raise ValueError("Expected train512/test256")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    inherited = args.first_round_calibration
    if inherited is not None and not (inherited / "selection.json").is_file():
        raise ValueError("Inherited first-round calibration must have a frozen selection")
    frozen = sources()
    write_bytes(
        output / "source-lock.json",
        json_bytes(
            {
                "sources": frozen,
                "plan": digest(args.plan),
                "first_round_calibration": str(inherited) if inherited else None,
                "inherited_selection_digest": digest(inherited / "selection.json")
                if inherited
                else None,
            }
        ),
        immutable=True,
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT / "src")))
    env["RSI_EVAL_MEMORY_FRACTION"] = str(plan.eval_memory_fraction)
    env["RSI_EVAL_CONTEXT_LENGTH"] = str(plan.context_length)
    excluded = {task_id for split in RsiSplit for task_id in getattr(tasks, split.value).task_ids}
    previous: list[Path] = []
    model, prompt = plan.model, Path(plan.prompt)
    for generation in range(1, 4):
        if sources() != frozen:
            raise ValueError("Source changed during dynamic experiment")
        round_root = output / f"g{generation}"
        round_root.mkdir(parents=True, exist_ok=True)
        candidates: list[SyntheticMathCase] = []
        scores: list[int | None] = []
        report_digests: list[str] = []
        probe_ids: list[str] = []
        calibration_root = inherited if generation == 1 and inherited is not None else round_root
        selected_root = calibration_root / "taskset"
        for batch in range(8):
            seed = 20261005 + generation * 10000 + batch * 100
            fresh = fresh_candidates(seed, excluded)
            probe = calibration_root / f"batch{batch}/taskset"
            if calibration_root != round_root and not probe.exists():
                raise ValueError("Inherited calibration is incomplete")
            if not probe.exists():
                freeze_tir_synthetic_probe(
                    parent,
                    probe,
                    seed=seed,
                    count=1024,
                    excluded_task_set_roots=tuple(previous),
                    candidates=fresh,
                )
            frozen_probe = load_task_set(probe)
            if frozen_probe.harness_evolve.task_ids != tuple(c.case.task_id for c in fresh):
                raise ValueError("Existing probe differs from fresh deterministic candidates")
            write_bytes(
                probe / "private/candidate-metadata.json", json_bytes(fresh), immutable=True
            )
            reports = tuple(
                calibration_root / f"batch{batch}/probe-{repeat}/report.json" for repeat in (1, 2)
            )
            for repeat, report in enumerate(reports, 1):
                if calibration_root != round_root and not report.exists():
                    raise ValueError("Inherited calibration report is missing")
                if not report.exists():
                    run(
                        [
                            "bash",
                            str(
                                ROOT
                                / "src/areal_rsi/infra/coordination/launch/run_shared512_eval.sh"
                            ),
                            model,
                            str(probe),
                            "harness_evolve",
                            f"dynamic-g{generation}-b{batch}-p{repeat}",
                            str(report.parent),
                            str(prompt),
                        ],
                        env,
                    )
            scores.extend(calibrated_scores(probe, (reports[0], reports[1]), model, prompt))
            candidates.extend(fresh)
            excluded.update(item.case.task_id for item in fresh)
            previous.append(probe)
            report_digests.extend(digest(path) for path in reports)
            probe_ids.append(frozen_probe.task_set_id)
            try:
                selection = select_model_calibrated_cases(
                    tuple(candidates),
                    tuple(scores),
                    count=512,
                    seed=seed,
                    minimum_one_pass=32 if generation == 1 else 128,
                    maximum_per_recipe=48,
                )
            except ValueError as error:
                print(f"Round {generation} batch {batch}: {error}", flush=True)
                continue
            if calibration_root != round_root and not selected_root.exists():
                raise ValueError("Inherited selected task set is missing")
            if not selected_root.exists():
                freeze_tir_synthetic_training(
                    parent,
                    selected_root,
                    selection.cases,
                    seed=seed,
                    probe_task_set_ids=tuple(probe_ids),
                    probe_report_digests=tuple(report_digests),
                    selected_pass_counts=(selection.zero_passes, selection.one_pass),
                    excluded_task_set_roots=(),
                )
            selected_tasks = load_task_set(selected_root)
            if selected_tasks.model_train.task_ids != tuple(
                c.case.task_id for c in selection.cases
            ):
                raise ValueError("Existing training set differs from calibrated selection")
            write_bytes(
                round_root / "selection.json",
                json_bytes(
                    {
                        "model": model,
                        "prompt_digest": digest(prompt),
                        "candidate_count": len(candidates),
                        "selected_zero_pass": selection.zero_passes,
                        "recipes": {
                            recipe: sum(c.recipe == recipe for c in selection.cases)
                            for recipe in sorted({c.recipe for c in candidates})
                        },
                        "selected_cases": [
                            {
                                "task_id": c.case.task_id,
                                "family": c.family.value,
                                "recipe": c.recipe,
                                "structure_level": c.difficulty,
                            }
                            for c in selection.cases
                        ],
                        "selected_one_pass": selection.one_pass,
                        "probe_report_digests": report_digests,
                        "task_set_id": selected_tasks.task_set_id,
                    }
                ),
                immutable=True,
            )
            break
        else:
            raise ValueError(
                "Eight batches insufficient; revise generator difficulty before training"
            )
        current = load_task_set(selected_root)
        if current.model_dev != tasks.model_dev:
            raise ValueError("Fixed test partition changed")
        round_plan = replace(
            plan,
            name=f"dynamic_g{generation}",
            model=model,
            prompt=str(prompt),
            task_set=str(selected_root),
            minimum_rounds=1,
            maximum_rounds=1,
        )
        plan_path = round_root / "plan.json"
        write_bytes(plan_path, json_bytes(round_plan), immutable=True)
        run(
            [
                sys.executable,
                "-m",
                "areal_rsi.infra.coordination.continuation",
                "--plan",
                str(plan_path),
                "--output",
                str(round_root / "run"),
            ],
            env,
        )
        result = read_record(round_root / "run/g1-result.json", RoundResult)
        # Always complete all three updates, as in the fixed-data three-round comparison.
        after = read_record(round_root / "run/g1-eval/report.json", AgentEvaluationReport)
        model, prompt = after.model, round_root / "run/g1-eval/harness/prompt.txt"
        write_bytes(
            round_root / "result.json",
            json_bytes(
                replace(
                    result,
                    generation=generation,
                    stop=False,
                    next_model=model,
                    next_prompt=str(prompt),
                )
            ),
            immutable=True,
        )
        print(f"Round {generation}: {result.after_correct}/256", flush=True)


if __name__ == "__main__":
    main()
