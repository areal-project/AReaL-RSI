"""CLI 与 DSH 薄入口；聊天由 DSH TUI 处理，实验经本地 MCP 管理。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
from pathlib import Path

from areal_rsi.api.base import ExperimentPlan, ExperimentRun, RunState
from areal_rsi.app import LocalApp
from areal_rsi.base.storage import FileRecords, decode, encode
from areal_rsi.config import ModelConfigurationError, init_config, load_user_config


def data_root() -> Path:
    return Path(os.environ.get("RSI_DATA_ROOT", ".rsi/local")).resolve()


async def run_experiment(plan_file: str | None, resume: str | None) -> int:
    root = data_root()
    app = await LocalApp.create(root, lambda text: print(text, flush=True))
    try:
        if resume:
            previous = await app.service.status(resume)
            if previous is None:
                raise KeyError(resume)
            await app.bind_plan(previous.plan)
            run = await app.service.resume(resume)
        else:
            assert plan_file is not None
            plan = decode(ExperimentPlan, json.loads(Path(plan_file).read_text()))
            await app.bind_plan(plan)
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
    parser = argparse.ArgumentParser(description="RSI 本地研发入口")
    commands = parser.add_subparsers(dest="command")
    config = commands.add_parser("config", help="管理 ~/.areal_rsi/config.json")
    config.add_argument("action", choices=["init"])
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
    execute = commands.add_parser("exec", help="澄清、确认并持续执行生产目标")
    execute.add_argument("description", nargs="?")
    execute.add_argument("--spec")
    execute.add_argument("--target", choices=["harness", "artifact", "model"])
    execute.add_argument("--artifact")
    execute.add_argument("--yes", action="store_true")
    execute.add_argument("--json", action="store_true")
    execute.add_argument("--detach", action="store_true")
    goals = commands.add_parser("goal", help="Goal 生命周期")
    goal_actions = goals.add_subparsers(dest="action", required=True)
    for action in ("status", "answer", "pause", "resume", "cancel", "export"):
        sub = goal_actions.add_parser(action)
        sub.add_argument("goal_id")
        sub.add_argument("--json", action="store_true")
        if action == "export":
            sub.add_argument("--destination", type=Path, required=True)
        if action == "answer":
            sub.add_argument("--answers", required=True)
            sub.add_argument("--yes", action="store_true")
    skills = commands.add_parser("skill", help="版本化 Research Skill 库")
    skill_actions = skills.add_subparsers(dest="action", required=True)
    skill_actions.add_parser("list")
    capture = skill_actions.add_parser("capture")
    capture.add_argument("--goal", required=True)
    capture.add_argument("--max-seconds", type=float, default=600)
    for action in ("inspect", "validate", "activate", "deprecate"):
        sub = skill_actions.add_parser(action)
        sub.add_argument("skill_id")
        sub.add_argument("--version", required=True)
        if action == "deprecate":
            sub.add_argument("--reason", required=True)
    goal_worker = commands.add_parser("_goal-worker", help=argparse.SUPPRESS)
    goal_worker.add_argument("--root", type=Path, required=True)
    goal_worker.add_argument("--goal-id", required=True)
    goal_worker.add_argument("--revision", type=int, required=True)
    goal_worker.add_argument("--lock-fd", type=int, required=True)
    args = parser.parse_args()
    try:
        if args.command == "config":
            print(f"配置文件：{init_config()}（已有文件不会覆盖）")
            return
        load_user_config()
    except (ModelConfigurationError, OSError) as exc:
        if args.command in {"exec", "goal", "skill"} and getattr(args, "json", False):
            from areal_rsi.ui.goal_cli import output

            output({"schema_version": 1, "type": "error", "error": str(exc)})
            raise SystemExit(2) from None
        parser.exit(2, f"配置错误：{exc}\n")
    if args.command in {"exec", "goal", "skill"}:
        from areal_rsi.ui.goal_cli import dispatch, output

        try:
            raise SystemExit(asyncio.run(dispatch(args, data_root())))
        except KeyboardInterrupt:
            raise SystemExit(130) from None
        except (ValueError, TypeError, KeyError, OSError) as exc:
            import sys

            output(
                {
                    "schema_version": 1,
                    "type": "error",
                    "error": str(exc),
                    "goal_id": getattr(args, "goal_id", None),
                },
                stream=sys.stdout if getattr(args, "json", False) else sys.stderr,
            )
            raise SystemExit(2) from None
    if args.command == "_goal-worker":
        from areal_rsi.goals import run_goal_worker

        raise SystemExit(
            asyncio.run(run_goal_worker(args.root, args.goal_id, args.revision, args.lock_fd))
        )
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
                encode(FileRecords(data_root() / "run-states").list(ExperimentRun)),
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
            "请运行 rsi config init，并填写 ~/.areal_rsi/config.json，然后运行：\n"
            "  uv run --locked rsi tui\n",
        )
    raise SystemExit(code)
