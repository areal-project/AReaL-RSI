"""AReaL-RSI command line entry point."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from areal_rsi.config import RSIRunConfig
from areal_rsi.controller import LocalRSIController
from areal_rsi.memory import Memory
from areal_rsi.services import (
    AReaLService,
    ClaudeResearchAgent,
    ClaudeResearchConfig,
    EnvArenaClient,
    MockAReaLService,
    MockEnvClient,
    MockResearchAgent,
    OpenAIResearchAgent,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="areal-rsi")
    parser.add_argument("--version", action="store_true", help="Print version and exit.")
    subparsers = parser.add_subparsers(dest="command")

    run = subparsers.add_parser("run", help="Run an RSI loop from a config file.")
    run.add_argument("--config", required=True, help="Path to RSI config YAML/JSON.")
    run.add_argument("--run-root", help="Override run.run_root from the RSI config.")

    run_mock = subparsers.add_parser("run-mock", help="Run one local mock RSI iteration.")
    run_mock.add_argument("--ckpt", default="ckpt_000")
    run_mock.add_argument("--benchmark", "--task-set", dest="benchmark", default="swe_verified")
    run_mock.add_argument("--method", default="sft", choices=["sft", "opd"])
    run_mock.add_argument("--harness", default="mock_harness")
    run_mock.add_argument("--run-root", default="runs")

    run_loop_mock = subparsers.add_parser("run-loop-mock", help="Run a local mock multi-iteration RSI loop.")
    run_loop_mock.add_argument("--ckpt", default="ckpt_000")
    run_loop_mock.add_argument("--benchmark", "--task-set", dest="benchmark", default="swe_verified")
    run_loop_mock.add_argument("--method", default="sft", choices=["sft", "opd"])
    run_loop_mock.add_argument("--harness", default="mock_harness")
    run_loop_mock.add_argument("--max-iterations", type=int, default=3)
    run_loop_mock.add_argument("--run-root", default="runs")

    status = subparsers.add_parser("status", help="Print Run state as JSON.")
    status.add_argument("run_id", help="User-started Run id.")
    status.add_argument("--run-root", default="runs")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        from areal_rsi import __version__

        print(__version__)
        return 0
    if args.command == "run-mock":
        state = _run_mock(args)
        print(json.dumps(state.to_dict(), indent=2, ensure_ascii=False, default=repr))
        return 0
    if args.command == "run":
        state = _run_config(args)
        print(json.dumps(state.to_dict(), indent=2, ensure_ascii=False, default=repr))
        return 0
    if args.command == "run-loop-mock":
        state = _run_loop_mock(args)
        print(json.dumps(state.to_dict(), indent=2, ensure_ascii=False, default=repr))
        return 0
    if args.command == "status":
        controller = LocalRSIController(run_root=args.run_root)
        state = controller.get_status(args.run_id)
        print(json.dumps(state.to_dict(), indent=2, ensure_ascii=False, default=repr))
        return 0
    parser.print_help()
    return 0


def _run_mock(args: argparse.Namespace):
    run_root = Path(args.run_root)
    controller = LocalRSIController(
        run_root=run_root,
        areal=MockAReaLService(),
        envarena=MockEnvClient(),
        research_agent=MockResearchAgent(),
    )
    return controller.run_iteration(
        ckpt=args.ckpt,
        benchmark=args.benchmark,
        method=args.method,
        harness_config={"harness": args.harness},
    )


def _run_config(args: argparse.Namespace):
    config = RSIRunConfig.load(args.config)
    run_root = Path(
        args.run_root or config.data.get("run", {}).get("run_root", "runs")
    )
    controller = _controller_from_rsi_config(config, run_root, None)
    return controller.run_config(
        config,
        config_metadata={
            "base_config_path": str(config.source_path) if config.source_path else None,
            "base_config_hash": config.source_hash,
            "overrides": {},
        },
    )


def _run_loop_mock(args: argparse.Namespace):
    run_root = Path(args.run_root)
    controller = LocalRSIController(
        run_root=run_root,
        areal=MockAReaLService(),
        envarena=MockEnvClient(),
        research_agent=MockResearchAgent(),
    )
    return controller.run_loop(
        initial_ckpt=args.ckpt,
        benchmark=args.benchmark,
        method=args.method,
        harness_config={"harness": args.harness},
        max_iterations=args.max_iterations,
    )


def _controller_from_rsi_config(
    config: RSIRunConfig,
    run_root: Path,
    memory: Memory | None,
) -> LocalRSIController:
    """Compose configured services at the CLI boundary before running a loop."""

    services = config.data.get("services", {})
    if not isinstance(services, dict):
        raise ValueError("RSI config services section must be a mapping.")
    areal_spec = _service_spec(services, "areal")
    envarena_spec = _service_spec(services, "envarena")
    research_spec = _service_spec(services, "research")

    areal_config_path = _service_config_path(config, "areal", areal_spec)
    if areal_config_path is None:
        areal = MockAReaLService()
    else:
        areal = AReaLService.from_yaml(areal_config_path)

    envarena_config_path = _service_config_path(config, "envarena", envarena_spec)
    envarena = (
        EnvArenaClient.from_yaml(envarena_config_path)
        if envarena_config_path is not None
        else MockEnvClient(task_set=_load_mock_task_set(envarena_spec, config))
    )
    research_agent = _research_agent_from_spec(research_spec)
    return LocalRSIController(
        run_root=run_root,
        areal=areal,
        envarena=envarena,
        research_agent=research_agent,
        memory=memory,
    )


def _load_mock_task_set(
    envarena_spec: dict[str, object],
    config: RSIRunConfig,
) -> list[dict[str, object]] | None:
    """Load a task set so mock trajectory task_ids match OPD train instance_ids.

    Reads a JSONL whose rows carry ``instance_id`` (or ``task_id``) and chat
    ``messages``; each becomes one emitted mock task. Optional
    ``services.envarena.task_set_limit`` caps how many tasks are used.
    """

    path_value = envarena_spec.get("task_set_path")
    if not path_value:
        return None
    if not isinstance(path_value, str):
        raise ValueError("services.envarena.task_set_path must be a string path.")
    path = Path(path_value).expanduser()
    if not path.is_absolute() and config.source_path is not None:
        path = config.source_path.parent / path
    limit_value = envarena_spec.get("task_set_limit")
    limit = int(limit_value) if limit_value else None
    entries: list[dict[str, object]] = []
    with path.open(encoding="utf-8") as task_file:
        for index, line in enumerate(task_file):
            if limit is not None and index >= limit:
                break
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            task_id = row.get("instance_id") or row.get("task_id")
            if not task_id:
                continue
            messages = row.get("messages") or []
            user_content = next(
                (
                    message.get("content")
                    for message in messages
                    if isinstance(message, dict) and message.get("role") == "user"
                ),
                None,
            )
            entries.append({"task_id": str(task_id), "user_content": user_content})
    return entries or None


def _service_spec(services: dict[str, object], name: str) -> dict[str, object]:
    value = services.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"RSI config services.{name} must be a mapping.")
    return value


def _service_config_path(
    config: RSIRunConfig,
    service_name: str,
    service_spec: dict[str, object],
) -> Path | None:
    value = service_spec.get("config_path")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"RSI config services.{service_name}.config_path must be a non-empty string.")
    path = Path(value).expanduser()
    if not path.is_absolute() and config.source_path is not None:
        path = config.source_path.parent / path
    return path


def _research_agent_from_spec(spec: dict[str, object]):
    backend = str(spec.get("backend", "mock"))
    if backend == "mock":
        return MockResearchAgent()
    if backend == "openai":
        return OpenAIResearchAgent()
    if backend in {"claude", "claude_agent_sdk"}:
        return ClaudeResearchAgent(
            ClaudeResearchConfig.from_mapping(dict(spec))
        )
    raise ValueError(f"Unsupported research service backend: {backend!r}.")


if __name__ == "__main__":
    raise SystemExit(main())
