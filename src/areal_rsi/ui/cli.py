"""Terminal and headless entry points for a durable research run."""

from __future__ import annotations

import argparse
import asyncio
import json
import shlex
from pathlib import Path

from areal_rsi.api.research import TaskBrief
from areal_rsi.infra.coordination.config import ResearchConfig, load_config
from areal_rsi.infra.research import ResearchSystem
from areal_rsi.infra.storage.paths import data_root


async def run_research(
    *,
    config_file: Path | None = None,
    resume: str | None = None,
    root: Path | None = None,
) -> int:
    if (config_file is None) == (resume is None):
        raise ValueError("Choose exactly one of --config or --resume")
    system = ResearchSystem(data_root(root))
    try:
        if resume:
            run_id = resume
            await system.resume(run_id)
        else:
            assert config_file is not None
            config = load_config(config_file)
            config.runtime()
            run_id = await system.create(config)
            await system.start(run_id)
        command = (
            f"rsi tui --data-root {shlex.quote(str(system.root))} --resume {shlex.quote(run_id)}"
        )
        print(json.dumps({"run_id": run_id, "resume_command": command}), flush=True)
        cursor = 0
        while True:
            detail = await system.detail(run_id, after=cursor)
            for event in detail["events"]:
                print(json.dumps(event, ensure_ascii=False), flush=True)
            cursor = detail["event_cursor"]
            if not detail["worker_alive"]:
                return 0 if detail["status"] in {"succeeded", "completed"} else 1
            if detail["status"] == "paused" or detail.get("waiting_for_user"):
                print(f"等待操作员输入：{command}", flush=True)
                return 3
            if detail["mode"] != "legacy_passk" and any(
                agent["state"] == "failed" for agent in detail["agents"]
            ):
                return 1
            await asyncio.sleep(0.2)
    finally:
        await system.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="RSI：Leader goal 与 legacy pass@k")
    commands = parser.add_subparsers(dest="command")
    parser.set_defaults(command="tui", root=None, config=None, resume=None, workspace=None)
    tui = commands.add_parser("tui", help="交互终端（默认入口）")
    tui.add_argument("--data-root", dest="root", type=Path)
    source = tui.add_mutually_exclusive_group()
    source.add_argument("--config", type=Path)
    source.add_argument("--resume")
    tui.add_argument("--workspace", type=Path, help="以 Git 工作区为研究起点")
    research = commands.add_parser("research", help="无界面运行，输出 JSON 事件")
    research.add_argument("--data-root", dest="root", type=Path)
    source = research.add_mutually_exclusive_group(required=True)
    source.add_argument("--config", type=Path)
    source.add_argument("--resume")
    setup = commands.add_parser("prepare", help="准备 harness 优化示例")
    setup.add_argument("benchmark", choices=("arithmetic", "livecodebench"))
    setup.add_argument("--directory", type=Path, required=True)
    setup.add_argument("--arena-config", type=Path)
    setup.add_argument("--time-limit", type=float, default=1200)
    setup.add_argument("--backend", choices=("standard", "codex", "runtime"), default="standard")
    preflight_parser = commands.add_parser("preflight", help="检查后端、工具和会话恢复")
    preflight_parser.add_argument("--config", type=Path)
    preflight_parser.add_argument("--backend", choices=("standard", "codex", "runtime"))
    preflight_parser.add_argument("--data-root", dest="root", type=Path)
    preflight_parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    try:
        if args.command == "tui":
            from areal_rsi.ui.tui import ResearchTUI

            if args.workspace and (args.config or args.resume):
                raise ValueError("--workspace cannot override --config or --resume")
            config = (
                load_config(args.config)
                if args.config
                else ResearchConfig(
                    TaskBrief("新的研究", ""),
                    workspace_dir=str(args.workspace.resolve()) if args.workspace else None,
                )
            )
            ResearchTUI(ResearchSystem(data_root(args.root)), config, args.resume).run()
            code = 0
        elif args.command == "research":
            code = asyncio.run(
                run_research(
                    config_file=args.config,
                    resume=args.resume,
                    root=args.root,
                )
            )
        elif args.command == "prepare":
            from areal_rsi.infra.adapters.benchmark_research import prepare

            path = asyncio.run(
                prepare(
                    {"arithmetic": "math", "livecodebench": "lcb"}[args.benchmark],
                    args.directory,
                    arena_config=args.arena_config,
                    max_wall_seconds=args.time_limit,
                    backend=args.backend,
                )
            )
            print(path)
            code = 0
        else:
            from areal_rsi.ui.preflight import preflight

            config = (
                load_config(args.config)
                if args.config
                else ResearchConfig(
                    TaskBrief("Runtime preflight", "Check tools and durable sessions")
                )
            )
            config.pin_model_settings()
            if args.backend:
                config.backend = args.backend
            result = asyncio.run(preflight(config, root=args.root, timeout=args.timeout))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            code = 0
    except (ValueError, OSError) as exc:
        parser.exit(2, f"研究配置或启动错误：{exc}\n")
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    raise SystemExit(code)
