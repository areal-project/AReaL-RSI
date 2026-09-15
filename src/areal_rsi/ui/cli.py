"""Terminal and Web entry points for the same persistent research engine."""

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


def data_root() -> Path:
    return Path(os.environ.get("RSI_DATA_ROOT") or ".rsi/research-lab").resolve()


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
    if (detail.get("finalization") or {}).get("status") == "running":
        return None
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
            if status in {"succeeded", "failed", "stopped", "cancelled"} and not detail.get(
                "worker_alive"
            ):
                if (detail.get("finalization") or {}).get("status") == "failed":
                    return 1
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
    server.invoke("recover", {})
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
    parser = argparse.ArgumentParser(description="RSI 持续研究：TUI / Web / CLI 共用一个执行引擎")
    commands = parser.add_subparsers(dest="command")
    for name in ("tui", "workbench"):
        command = commands.add_parser(name)
        command.add_argument("--port", type=int, default=8197)
        command.add_argument("--config", type=Path)
        if name == "workbench":
            command.add_argument("--simulate-gpu", action="store_true")
        if name == "tui":
            command.add_argument("--resume", help="绑定已有研究 run_id")
            command.add_argument("--session", help="恢复原 DSH 对话 session ID")
            command.add_argument("--setup-only", action="store_true", help="只安装 DSH TUI plugin")
    research = commands.add_parser("research", help="前台运行或恢复研究")
    source = research.add_mutually_exclusive_group(required=True)
    source.add_argument("--config", type=Path)
    source.add_argument("--example", choices=("kernelbench",))
    source.add_argument("--resume")
    research.add_argument("--simulate-gpu", action="store_true")
    research.add_argument("--timeout", type=float)
    research.add_argument("--poll-interval", type=float, default=2)
    prepare = commands.add_parser("prepare", help="准备数学或 LCB 的真实研究配置")
    prepare.add_argument("--case", choices=("arithmetic", "gsm8k", "lcb"), required=True)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--dataset", type=Path, default=Path(".rsi/datasets/gsm8k"))
    prepare.add_argument("--limit", type=int, default=2)
    prepare.add_argument("--max-agents", type=int, default=1)
    prepare.add_argument(
        "--remote-config",
        type=Path,
        default=Path(__file__).parents[1] / "resources/presets/livecodebench.yaml",
    )
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            from areal_rsi.adapters.harness_cases import prepare as prepare_case

            values = vars(args).copy()
            values.pop("command")
            asyncio.run(prepare_case(**values))
            print(f"研究配置：{args.output.resolve()}")
            code = 0
        elif args.command == "workbench":
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
        else:
            from areal_rsi.ui.dsh import launch_tui

            code = launch_tui(
                data_root(),
                port=getattr(args, "port", 8197),
                config=getattr(args, "config", None),
                resume=getattr(args, "resume", None),
                session=getattr(args, "session", None),
                setup_only=getattr(args, "setup_only", False),
            )
    except (ValueError, OSError) as exc:
        parser.exit(2, f"研究配置或启动错误：{exc}\n")
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    raise SystemExit(code)
