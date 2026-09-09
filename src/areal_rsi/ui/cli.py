"""CLI 与 DSH 薄入口；聊天由 DSH TUI 处理，实验经本地 MCP 管理。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
from pathlib import Path

from areal_rsi.api.base import ExperimentPlan, ExperimentRun, RunState
from areal_rsi.app import create_app
from areal_rsi.base.storage import FileRecords, decode, encode
from areal_rsi.config import ModelConfigurationError
from areal_rsi.experiment_config import environment_scope, experiment_roots, plan_environment


def data_root() -> Path:
    return Path(os.environ.get("RSI_DATA_ROOT") or ".rsi").resolve()


async def run_experiment(plan_file: str | None, resume: str | None) -> int:
    root = data_root()
    if resume:
        from areal_rsi.ui.workspace import ExperimentWorkspace

        controller = ExperimentWorkspace(root).locate(resume)
        root = controller.root
        plan = controller._run(resume).plan
    else:
        assert plan_file is not None
        path = Path(plan_file).resolve()
        value = json.loads(path.read_text())
        plan = decode(ExperimentPlan, value.get("plan", value))
        candidate = path.parent.parent if path.parent.name == "experiment-plans" else path.parent
        if (candidate / "launch.json").exists():
            root = candidate
    with environment_scope(plan_environment(root, plan.config)):
        return await _execute(root, plan, resume)


async def _execute(root: Path, plan: ExperimentPlan, resume: str | None) -> int:
    app = await create_app(root, lambda text: print(text, flush=True))
    try:
        await app.bind_plan(plan)
        if resume:
            run = await app.service.resume(resume)
        else:
            run = await app.service.start(plan)
        print(f"实验已启动 run={run.run_id}", flush=True)
        if run.run_id in app.service.tasks:
            task = app.service.tasks[run.run_id]
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, task.cancel)
            run = await task
        print(json.dumps(encode(run), ensure_ascii=False), flush=True)
        return 0 if run.state == RunState.SUCCEEDED else 1
    finally:
        await app.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="RSI 研发入口（local / remote）")
    commands = parser.add_subparsers(dest="command")
    tui = commands.add_parser("tui", help="打开 DSH 原生 TUI")
    tui.add_argument("--resume", dest="session_id", help="恢复 DSH 对话 Session")
    tui.add_argument("--setup-only", action="store_true", help="安装并准备本地 DSH profile")
    worker = commands.add_parser("_experiment-worker", help=argparse.SUPPRESS)
    worker.add_argument("--root", type=Path, required=True)
    worker.add_argument("--run-id", required=True)
    worker.add_argument("--token", required=True)
    worker.add_argument("--lock-fd", type=int, required=True)
    mcp = commands.add_parser("_mcp", help=argparse.SUPPRESS)
    mcp.add_argument("--root", type=Path, required=True)
    run = commands.add_parser("run", help="执行已经确认的计划")
    run.add_argument("--plan", required=True)
    resume = commands.add_parser("resume", help="恢复到最后一个已完成轮次")
    resume.add_argument("run_id")
    commands.add_parser("status", help="列出本地实验")
    web = commands.add_parser("web", help="启动本地只读研究面板，不启动实验")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--run", dest="run_id")
    args = parser.parse_args()
    if args.command == "_mcp":
        from areal_rsi.ui.mcp import serve as serve_mcp

        serve_mcp(args.root)
        return
    if args.command == "_experiment-worker":
        from areal_rsi.ui.experiments import run_worker

        raise SystemExit(asyncio.run(run_worker(args.root, args.run_id, args.token, args.lock_fd)))
    if args.command == "web":
        from areal_rsi.ui.web import serve

        serve(data_root(), port=args.port, run_id=args.run_id)
        return
    if args.command == "status":
        print(
            json.dumps(
                encode(
                    [
                        run
                        for root in experiment_roots(data_root())
                        for run in FileRecords(root / "run-states").list(ExperimentRun)
                    ]
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    try:
        if args.command == "run":
            code = asyncio.run(run_experiment(args.plan, None))
        elif args.command == "resume":
            code = asyncio.run(run_experiment(None, args.run_id))
        else:
            from areal_rsi.ui.dsh import launch_tui

            code = launch_tui(
                data_root(),
                resume=getattr(args, "session_id", None),
                setup_only=getattr(args, "setup_only", False),
            )
    except ModelConfigurationError as exc:
        parser.exit(
            2,
            f"配置错误：{exc}\n"
            "可填写本地 .env（参考 .env.example），然后运行：\n"
            "  uv run --env-file .env --locked rsi tui\n",
        )
    raise SystemExit(code)
