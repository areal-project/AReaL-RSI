"""CLI entry points for the ResearchApp workbench, research runs and preflight."""

from __future__ import annotations

import argparse
import asyncio
import json
import shlex
import signal
from contextlib import suppress
from pathlib import Path
from types import FrameType
from typing import Any

from areal_rsi.base.paths import data_root


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
    if detail.get("mode") == "passk" and detail.get("status") == "running":
        # Backoff or a gap between native turns is not a research stopping rule.
        return None
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
    root: Path | None = None,
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
    root = data_root(root)
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


def serve_workbench(
    *, port: int, config_file: Path | None, simulate_gpu: bool, root: Path | None = None
) -> int:
    from areal_rsi.coordination.config import load_config
    from areal_rsi.research_app import ResearchApp
    from areal_rsi.ui.workbench import WorkbenchServer

    config = load_config(config_file) if config_file else None
    app = ResearchApp(data_root(root), config=config, allow_simulation=simulate_gpu)
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
    parser = argparse.ArgumentParser(description="RSI 持续研究：工作台与 Research CLI")
    commands = parser.add_subparsers(dest="command")
    parser.set_defaults(command="workbench", port=8197, root=None, config=None, simulate_gpu=False)
    workbench = commands.add_parser(
        "workbench", aliases=["web"], help="打开可交互的本地研究工作台（默认入口）"
    )
    workbench.add_argument("--port", type=int, default=8197)
    workbench.add_argument("--data-root", "--root", dest="root", type=Path)
    workbench.add_argument("--config", type=Path, help="可信操作员配置 JSON")
    workbench.add_argument(
        "--simulate-gpu", action="store_true", help="允许模拟 GPU/Task 作业；研究模型仍调用真实 API"
    )
    research = commands.add_parser("research", help="运行开放式多 Agent 研究，并保留可恢复状态")
    research.add_argument("--data-root", "--root", dest="root", type=Path)
    source = research.add_mutually_exclusive_group(required=True)
    source.add_argument("--config", type=Path, help="可信操作员配置 JSON")
    source.add_argument("--example", choices=("kernelbench",), help="使用研究模拟示例")
    source.add_argument("--resume", help="恢复 run 的已归档配置与会话")
    research.add_argument(
        "--simulate-gpu", action="store_true", help="模拟 GPU/Task 作业；研究模型仍调用真实 API"
    )
    research.add_argument("--timeout", type=float, help="本次前台运行秒数；到时保留状态供恢复")
    research.add_argument("--poll-interval", type=float, default=2, help="读取本地进展的间隔秒数")
    preflight_parser = commands.add_parser("preflight", help="检查实际研究后端、工具和会话重启")
    preflight_parser.add_argument("--config", type=Path, help="使用与研究相同的配置")
    preflight_parser.add_argument("--backend", choices=("standard", "codex", "runtime"))
    preflight_parser.add_argument("--data-root", "--root", dest="root", type=Path)
    preflight_parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    try:
        if args.command in {"workbench", "web"}:
            code = serve_workbench(
                port=args.port, config_file=args.config,
                simulate_gpu=args.simulate_gpu, root=args.root,
            )
        elif args.command == "preflight":
            from areal_rsi.api.research import TaskBrief
            from areal_rsi.coordination.config import ResearchConfig, load_config
            from areal_rsi.ui.preflight import preflight

            config = load_config(args.config) if args.config else ResearchConfig(
                TaskBrief("Runtime preflight", "Check tools and durable sessions")
            )
            config.pin_model_settings()
            if args.backend is not None:
                config.backend = args.backend
            result = asyncio.run(preflight(config, root=args.root, timeout=args.timeout))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            code = 0
        elif args.command == "research":
            code = asyncio.run(
                run_research(
                    config_file=args.config,
                    example=args.example,
                    resume=args.resume,
                    simulate_gpu=args.simulate_gpu,
                    timeout=args.timeout,
                    poll_interval=args.poll_interval,
                    root=args.root,
                )
            )
    except (ValueError, OSError) as exc:
        parser.exit(2, f"研究配置或启动错误：{exc}\n检查配置及 .env 后重试。\n")
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    raise SystemExit(code)
