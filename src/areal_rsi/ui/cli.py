"""CLI 与 DSH 薄入口；聊天由 DSH TUI 处理，实验经本地 MCP 管理。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import signal
from contextlib import suppress
from pathlib import Path
from types import FrameType
from typing import Any

from areal_rsi.api.base import ExperimentPlan, ExperimentRun, RunState
from areal_rsi.app import LocalApp
from areal_rsi.base.runs import FileRunStore
from areal_rsi.base.storage import decode, encode
from areal_rsi.config import ModelConfigurationError


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


def _workbench_command(root: Path, *, simulated: bool) -> str:
    return (
        f"RSI_DATA_ROOT={shlex.quote(str(root))} uv run --env-file .env --locked rsi workbench"
        + (" --simulate-gpu" if simulated else "")
    )


def _research_summary(detail: dict[str, Any], *, status: str | None = None) -> dict[str, Any]:
    best = detail.get("best") or {}
    return {
        "run_id": detail["run_id"],
        "status": status or detail["status"],
        "simulated": bool(detail.get("simulated")),
        "nodes": len(detail.get("nodes", [])),
        "active_agents": [
            agent["agent_id"]
            for agent in detail.get("agents", [])
            if agent.get("state") == "running"
        ],
        "best_score": best.get("score"),
        "best_candidate_id": best.get("candidate_id"),
        "budget": {
            key: detail.get("budget", {}).get(key, 0)
            for key in ("model_calls", "total_tokens", "verify_calls")
        },
    }


def _idle_reason(detail: dict[str, Any]) -> str | None:
    if detail.get("status") == "paused":
        return "paused"
    if (
        detail.get("worker_alive", detail.get("worker_active")) is False
        and detail.get("status") == "running"
    ):
        return "worker_stopped"
    if detail.get("pending_agents") or detail.get("busy"):
        return None
    agents = detail.get("agents", [])
    if any(agent.get("state") == "running" for agent in agents):
        return None
    if any(
        job.get("status", job.get("state")) in {"queued", "starting", "running", "pending"}
        for job in detail.get("jobs", [])
    ):
        return None
    if detail.get("waiting_for_user"):
        return "waiting_for_user"
    if any(agent.get("state") == "failed" for agent in agents):
        return "agent_failed"
    return "waiting"


async def run_research(
    *,
    config_file: Path | None = None,
    example: str | None = None,
    resume: str | None = None,
    simulate_gpu: bool = False,
    timeout: float | None = None,
    poll_interval: float = 2,
) -> int:
    """Run real research models; poll only stored state and preserve work on interruption."""
    from areal_rsi.coordination.config import load_config
    from areal_rsi.coordination.presets import example_config
    from areal_rsi.research_app import ResearchApp

    if sum(value is not None for value in (config_file, example, resume)) != 1:
        raise ValueError("Choose exactly one of --config, --example or --resume")
    if example and not simulate_gpu:
        raise ValueError(
            "Example presets require --simulate-gpu; use --config for real GPU execution"
        )
    if (timeout is not None and timeout <= 0) or poll_interval <= 0:
        raise ValueError("Timeout and polling interval must be positive")
    root = data_root()
    config = load_config(config_file) if config_file else None
    if example:
        config = example_config(example, repository=Path(__file__).resolve().parents[3])
    if config is not None:
        config.runtime()  # Validate the exact model endpoint/key before creating a new run.
    app = ResearchApp(root, config=config, allow_simulation=simulate_gpu)
    loop = asyncio.get_running_loop()
    stopped = asyncio.Event()
    received_signal: list[int] = []
    previous_handlers: dict[signal.Signals, Any] = {}

    def interrupt(sig: int) -> None:
        received_signal.append(sig)
        stopped.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            previous = signal.getsignal(sig)
            loop.add_signal_handler(sig, interrupt, sig)
            previous_handlers[sig] = previous
        except (NotImplementedError, RuntimeError, ValueError):
            pass
    began = loop.time()
    last_printed: str | None = None
    previous_idle: tuple[str, Any] | None = None
    detail: dict[str, Any] | None = None
    try:
        if resume:
            detail = await app.call("resume", {"run_id": resume})
        else:
            detail = await app.call("create", {"simulated": simulate_gpu})
            if not stopped.is_set():
                detail = await app.call("start", {"run_id": detail["run_id"]})
        run_id = detail["run_id"]
        while True:
            summary = _research_summary(detail)
            serialized = json.dumps(summary, ensure_ascii=False)
            if serialized != last_printed:
                print(serialized, flush=True)
                last_printed = serialized
            status = detail["status"]
            if status in {"succeeded", "failed", "stopped", "cancelled"}:
                return 0 if status == "succeeded" else 1
            if stopped.is_set() or (timeout is not None and loop.time() - began >= timeout):
                exit_status = "interrupted" if stopped.is_set() else "timeout"
                print(
                    json.dumps(
                        {
                            **_research_summary(detail, status=exit_status),
                            "saved_status": status,
                            "resume_command": (
                                f"RSI_DATA_ROOT={shlex.quote(str(root))} "
                                "uv run --env-file .env --locked rsi research --resume "
                                + shlex.quote(run_id)
                                + (" --simulate-gpu" if detail.get("simulated") else "")
                            ),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                return 128 + received_signal[-1] if received_signal else 124
            idle = _idle_reason(detail)
            marker = (idle, detail.get("event_cursor")) if idle else None
            if marker and marker == previous_idle:
                print(
                    json.dumps(
                        {
                            **_research_summary(detail, status=idle),
                            "saved_status": status,
                            "notice": "研究尚未完成；打开工作台查看问题、预算或 Agent 状态后继续。",
                            "workbench_command": _workbench_command(
                                root, simulated=bool(detail.get("simulated"))
                            ),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                return 1 if idle in {"agent_failed", "worker_stopped"} else 3
            previous_idle = marker
            wait = poll_interval
            if timeout is not None:
                wait = max(0, min(wait, timeout - (loop.time() - began)))
            with suppress(TimeoutError):
                await asyncio.wait_for(stopped.wait(), wait)
            detail = await app.call("detail", {"run_id": run_id})
    finally:
        try:
            await app.close()
        finally:
            for sig, previous in previous_handlers.items():
                loop.remove_signal_handler(sig)
                signal.signal(sig, previous)


def serve_workbench(*, port: int, config_file: Path | None, simulate_gpu: bool) -> int:
    from areal_rsi.coordination.config import load_config
    from areal_rsi.research_app import ResearchApp
    from areal_rsi.ui.workbench import WorkbenchServer

    config = load_config(config_file) if config_file else None
    app = ResearchApp(data_root(), config=config, allow_simulation=simulate_gpu)
    server = WorkbenchServer(app, port=port)
    previous = signal.getsignal(signal.SIGTERM)
    terminated = False

    def terminate(_sig: int, _frame: FrameType | None) -> None:
        nonlocal terminated
        terminated = True
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    print(f"研究工作台：{server.url}", flush=True)
    print("停止本地服务会保留研究状态；再次打开后可恢复。", flush=True)
    try:
        server.serve_forever()
        return 0
    except KeyboardInterrupt:
        return 143 if terminated else 130
    finally:
        signal.signal(signal.SIGTERM, previous)
        try:
            asyncio.run_coroutine_threadsafe(app.close(), server.loop).result(timeout=30)
        finally:
            server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="RSI 本地研发入口")
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
    workbench = commands.add_parser("workbench", help="打开可交互的本地研究工作台")
    workbench.add_argument("--port", type=int, default=8766)
    workbench.add_argument("--config", type=Path, help="可信操作员配置 JSON")
    workbench.add_argument(
        "--simulate-gpu", action="store_true", help="允许模拟 GPU/Task 作业；研究模型仍调用真实 API"
    )
    research = commands.add_parser("research", help="运行开放式多 Agent 研究，并保留可恢复状态")
    source = research.add_mutually_exclusive_group(required=True)
    source.add_argument("--config", type=Path, help="可信操作员配置 JSON")
    source.add_argument("--example", choices=("kernelbench", "cuhkx"), help="使用工作流模拟示例")
    source.add_argument("--resume", help="恢复 run 的已归档配置与会话")
    research.add_argument(
        "--simulate-gpu", action="store_true", help="模拟 GPU/Task 作业；研究模型仍调用真实 API"
    )
    research.add_argument("--timeout", type=float, help="本次前台运行秒数；到时保留状态供恢复")
    research.add_argument("--poll-interval", type=float, default=2, help="读取本地进展的间隔秒数")
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
                encode(FileRunStore(data_root()).list(ExperimentRun)),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    try:
        if args.command == "workbench":
            code = serve_workbench(
                port=args.port, config_file=args.config, simulate_gpu=args.simulate_gpu
            )
        elif args.command == "research":
            code = asyncio.run(
                run_research(
                    config_file=args.config,
                    example=args.example,
                    resume=args.resume,
                    simulate_gpu=args.simulate_gpu,
                    timeout=args.timeout,
                    poll_interval=args.poll_interval,
                )
            )
        elif args.command == "run":
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
    except (ValueError, OSError) as exc:
        if args.command not in {"workbench", "research"}:
            raise
        parser.exit(2, f"研究配置或启动错误：{exc}\n检查配置及 .env 后重试。\n")
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    raise SystemExit(code)
