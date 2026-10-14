"""Headless entry points for research runs and backend preflight."""

from __future__ import annotations

import argparse
import asyncio
import json
import shlex
import signal
from contextlib import suppress
from pathlib import Path
from typing import Any

from areal_rsi.infra.storage.paths import data_root


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
    from areal_rsi.infra.coordination.config import load_config
    from areal_rsi.infra.coordination.presets import example_config
    from areal_rsi.infra.research import ResearchSystem

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
    system = ResearchSystem(root, config=config, allow_simulation=simulate_gpu)
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
            detail = await system.call("resume", {"run_id": resume})
        else:
            detail = await system.call("create", {"simulated": simulate_gpu})
            if not stopped.is_set():
                detail = await system.call("start", {"run_id": detail["run_id"]})
        run_id = detail["run_id"]
        while True:
            summary = _research_summary(detail)
            serialized = json.dumps(summary, ensure_ascii=False)
            if serialized != last_printed:
                print(serialized, flush=True)
                last_printed = serialized
            status = detail["status"]
            if status in {"succeeded", "completed", "failed", "stopped", "cancelled"} and not (
                detail.get("worker_alive")
            ):
                if (detail.get("finalization") or {}).get("status") == "failed":
                    return 1
                return 0 if status in {"succeeded", "completed"} else 1
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
                            "notice": "研究尚未完成；检查保存的运行状态后使用 --resume 继续。",
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
            detail = await system.call("detail", {"run_id": run_id})
    finally:
        try:
            await system.close()
        finally:
            for sig, previous in previous_handlers.items():
                loop.remove_signal_handler(sig)
                signal.signal(sig, previous)


def main() -> None:
    parser = argparse.ArgumentParser(description="RSI Research CLI")
    commands = parser.add_subparsers(dest="command", required=True)
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
    setup = commands.add_parser("prepare", help="准备 harness 优化示例，不启动研究")
    setup.add_argument("benchmark", choices=("arithmetic", "livecodebench"))
    setup.add_argument("--directory", type=Path, required=True)
    setup.add_argument("--arena-config", type=Path)
    setup.add_argument("--time-limit", type=float, default=1200)
    setup.add_argument("--backend", choices=("standard", "codex", "runtime"), default="standard")
    preflight_parser = commands.add_parser("preflight", help="检查实际研究后端、工具和会话重启")
    preflight_parser.add_argument("--config", type=Path, help="使用与研究相同的配置")
    preflight_parser.add_argument("--backend", choices=("standard", "codex", "runtime"))
    preflight_parser.add_argument("--data-root", "--root", dest="root", type=Path)
    preflight_parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            from areal_rsi.infra.adapters.benchmark_research import prepare

            path = asyncio.run(prepare(
                {"arithmetic": "math", "livecodebench": "lcb"}[args.benchmark], args.directory,
                arena_config=args.arena_config, max_wall_seconds=args.time_limit,
                backend=args.backend,
            ))
            print(path)
            code = 0
        elif args.command == "preflight":
            from areal_rsi.api.research import TaskBrief
            from areal_rsi.infra.coordination.config import ResearchConfig, load_config
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
