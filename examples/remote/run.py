"""Run a small explicitly requested remote experiment using the normal Workflow."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
from pathlib import Path

from areal_rsi.api.base import EvolutionTarget, ExperimentPlan, RunState
from areal_rsi.app import create_app
from areal_rsi.base.storage import encode, write_json


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare or launch a real remote Harness experiment"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--backend", default=os.environ.get("RSI_REMOTE_BACKEND", "envarena"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument(
        "--goal", default="根据 TRAIN 证据改进远端能力配置，完成一轮端到端评测；不要求分数提升"
    )
    parser.add_argument(
        "--launch", action="store_true", help="Create remote resources and execute jobs"
    )
    args = parser.parse_args()
    os.environ["RSI_MODE"] = "remote"
    os.environ["RSI_REMOTE_BACKEND"] = args.backend
    os.environ["RSI_REMOTE_CONFIG"] = str(args.config.resolve())
    os.environ["RSI_DATA_ROOT"] = str(args.root.resolve())
    app = await create_app(args.root, lambda text: print(text, flush=True))
    try:
        plan = ExperimentPlan(
            args.goal,
            "harness-evolution",
            app.service.context.benchmark.name,
            app.baseline.solution_id,
            EvolutionTarget.HARNESS,
            args.iterations,
            research_agent_count=len(app.service.context.research_agents),
            stop_when_goal_reached=False,
            config=app.model_plan_config(),
        )
        await app.bind_plan(plan)
        write_json(args.root / "plan.json", plan)
        if not args.launch:
            print(json.dumps(encode(plan), ensure_ascii=False, indent=2))
            return 0
        run = await app.service.start(plan)
        print(f"run_id={run.run_id}", flush=True)
        task = app.service.tasks[run.run_id]
        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(sig, task.cancel)
        result = await task
        print(json.dumps(encode(result), ensure_ascii=False, indent=2), flush=True)
        return 0 if result.state == RunState.SUCCEEDED else 1
    finally:
        await app.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
