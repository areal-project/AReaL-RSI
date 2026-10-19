"""Ordinary conversations by default; RSI is an explicit, context-preserving transition."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from areal_rsi.api.control import encode_record
from areal_rsi.api.research import ProblemBinding, SessionPhase, SessionStatus
from areal_rsi.infra.artifacts import file_ref
from areal_rsi.infra.config import from_environment
from areal_rsi.infra.research import ResearchSystem


async def run(args: argparse.Namespace) -> int:
    system = ResearchSystem(args.data_root)
    try:
        if args.command in {"status", "finalize"}:
            if not args.resume:
                raise ValueError("Specify --resume run-ID")
            if args.command == "status":
                result = {
                    "session": encode_record(system.detail(args.resume)),
                    "candidates": [
                        encode_record(item) for item in system.candidate_status(args.resume)
                    ],
                }
            else:
                if not args.candidate:
                    raise ValueError("Final evaluation requires --candidate ID")
                result = encode_record(await system.finalize_problem(args.resume, args.candidate))
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return 0
        binding = None
        if (
            args.command == "research"
            and args.resume
            and (args.problem or args.config is not None or args.objective is not None)
        ):
            raise ValueError("Resume uses the frozen problem, config and objective")
        if args.command == "research" and not args.resume:
            if not args.problem or args.config is None or args.objective is None:
                raise ValueError("Research requires --problem, --config and --objective")
            binding = ProblemBinding(
                args.problem, file_ref(args.config), objective=args.objective.read_text()
            )
        run_id = args.resume or await system.create(
            from_environment(args.workspace), problem=binding
        )
        print(json.dumps({"run_id": run_id, "data_root": str(system.root)}), flush=True)
        if args.command == "research":
            if args.resume:
                await system.open(run_id)
            record = system.detail(run_id)
            if record.phase == SessionPhase.CHAT and record.next_phase is None:
                await system.prepare_problem(run_id)
                if args.resume:
                    await system.resume(run_id)
                assert record.problem is not None
                await system.enter_rsi(
                    run_id,
                    "用户要求立即开始执行以下研究目标，保存任务并交接 Leader。\n"
                    + record.problem.objective,
                )
            else:
                await system.resume(run_id)
        else:
            await system.open(run_id)
        if args.command == "doctor":
            print("Codex app-server handshake OK; no model call was made.")
            return 0
        if args.command == "learn":
            document = await system.learn(run_id, args.prompt)
            print(json.dumps(encode_record(document), ensure_ascii=False), flush=True)
            return 0
        if args.resume and args.command != "research":
            await system.resume(run_id)
        if args.command == "research":
            pass
        elif args.prompt:
            await system.send_message(run_id, args.prompt, images=args.image)
        elif args.image:
            await system.send_message(run_id, "", images=args.image)
        elif not args.resume:
            raise ValueError("areal-rsi run requires a prompt or --resume")
        record = system.detail(run_id)
        if record.status == SessionStatus.COMPLETED or (
            record.phase != SessionPhase.LEADER
            and record.status == SessionStatus.IDLE
            and not record.pending_inputs
            and not record.active_turn_id
            and not record.handoff_pending
            and record.next_phase is None
        ):
            return 0
        async for event in system.events(run_id):
            if event.kind != "status":
                print(json.dumps(encode_record(event), ensure_ascii=False), flush=True)
            record = system.detail(run_id)
            if record.status == SessionStatus.FAILED:
                return 1
            if record.status in {
                SessionStatus.WAITING,
                SessionStatus.PAUSED,
                SessionStatus.BUDGET_LIMITED,
                SessionStatus.STOPPED,
            }:
                return 2
            if (
                not record.active_turn_id
                and not record.pending_inputs
                and not record.handoff_pending
                and record.next_phase is None
                and record.status
                in {
                    SessionStatus.IDLE,
                    SessionStatus.COMPLETED,
                }
                and (
                    record.phase != SessionPhase.LEADER or record.status == SessionStatus.COMPLETED
                )
            ):
                if record.learning.status == "running":
                    await system.learn(run_id)
                return 0
        return 0
    finally:
        await system.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="原生 Codex 对话与 RSI goal")
    commands = parser.add_subparsers(
        dest="command", required=True, metavar="{tui,run,research,status,finalize,doctor,learn}"
    )
    for name, description in (
        ("tui", "交互终端（默认）"),
        ("run", "执行并输出 JSON 事件"),
        ("doctor", "检查本地 Codex 协议，不调用模型"),
        ("learn", "在独立会话总结经验（使用 --resume 指定来源）"),
        ("research", "启动或恢复绑定任务的 RSI 研究"),
        ("status", "读取绑定任务的会话与候选状态，不调用模型"),
        ("finalize", "独立验收冻结候选，不启动研究 Agent"),
        ("_ui", ""),
    ):
        command = (
            commands.add_parser(name, help=description)
            if description
            else commands.add_parser(name)
        )
        command.add_argument("--data-root", "--root", dest="data_root", type=Path)
        command.add_argument("--workspace", type=Path)
        command.add_argument("--resume")
        command.add_argument("--env-file", type=Path, default=Path(".env"))
        command.set_defaults(prompt="", image=[])
        if name == "research":
            command.add_argument("--problem", help="任务实现 module:Class")
            command.add_argument("--config", type=Path)
            command.add_argument("--objective", type=Path)
        if name == "finalize":
            command.add_argument("--candidate")
        if name in {"run", "learn"}:
            command.add_argument("prompt", nargs="?", default="")
            command.add_argument("--image", action="append", type=Path, default=[])
    argv = sys.argv[1:]
    if not argv or (argv[0].startswith("-") and argv[0] not in {"--help", "-h"}):
        argv.insert(0, "tui")
    args = parser.parse_args(argv)
    load_dotenv(args.env_file, override=False)
    try:
        if args.command == "learn" and not args.resume:
            raise ValueError("areal-rsi learn requires --resume run-ID")
        if args.resume and args.workspace:
            raise ValueError("恢复会话时沿用原工作目录，不能同时指定 --workspace")
        if args.command == "tui":
            from areal_rsi.ui.tui import launch

            code = launch(args)
        elif args.command == "_ui":
            from areal_rsi.ui.tui import serve

            code = asyncio.run(serve(args))
        else:
            code = asyncio.run(run(args))
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        code = 1
    except KeyboardInterrupt:
        code = 130
    raise SystemExit(code)
