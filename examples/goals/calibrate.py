"""Development-only baseline preflight; never opens FINAL cases."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

from areal_rsi.api.base import BenchmarkSplit, ExperimentPlan, ExperimentRun, RunState, Task
from areal_rsi.api.goal import GoalSpec
from areal_rsi.app import LocalApp
from areal_rsi.base.storage import FileRecords, decode, encode, new_id, write_json
from areal_rsi.config import load_user_config
from areal_rsi.workflows.evolution import HarnessEvolutionWorkflow


async def calibrate(root: Path, count: int, benchmark_name: str, max_seconds: int) -> None:
    load_user_config()
    spec = json.loads(Path(__file__).with_name("harness.json").read_text())
    spec["benchmark"] = benchmark_name
    spec["acceptance"]["verifier_version"] = benchmark_name + "-verifier"
    spec["assets"]["harness_path"] = str(Path(__file__).with_name("minimal-harness").resolve())
    goal = decode(GoalSpec, spec)
    app = await LocalApp.create(root, lambda _: None, goal=goal)
    try:
        benchmark = app.service.context.benchmark

        class Subset:
            name = benchmark.name

            async def list_tasks(self, split: BenchmarkSplit) -> Sequence[Task]:
                assert split != BenchmarkSplit.FINAL
                return (await benchmark.list_tasks(split))[:count]

        app.service.context.benchmark = Subset()
        plan = ExperimentPlan(
            goal.description,
            "harness-evolution",
            benchmark.name,
            app.baseline.solution_id,
            goal.target,
            1,
            config=app.model_plan_config(),
        )
        run = ExperimentRun(new_id("run"), plan, RunState.PLANNED, app.baseline.solution_id)
        records = FileRecords(root / "run-states")
        records.save(run.run_id, run)
        workflow = HarnessEvolutionWorkflow(root / "runs", records, run, print)
        async with asyncio.timeout(max_seconds):
            report, _ = await workflow.evaluate(
                app.service.context,
                app.baseline,
                BenchmarkSplit.VALIDATE,
                root / "runs" / run.run_id / "calibration",
            )
        result = {
            "classification": "development_preflight",
            "final_tasks_evaluated": 0,
            "report": encode(report),
            "baseline": encode(app.baseline),
            "model_runtime": app.model_plan_config(),
            "submitted_spec": spec,
        }
        write_json(root / "calibration-report.json", result)
        print(json.dumps(result), flush=True)
    finally:
        await app.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--count", type=int, default=6)
    parser.add_argument("--benchmark", default="structured-arithmetic-v2")
    parser.add_argument("--max-seconds", type=int, default=1800)
    args = parser.parse_args()
    asyncio.run(calibrate(args.root.resolve(), args.count, args.benchmark, args.max_seconds))
