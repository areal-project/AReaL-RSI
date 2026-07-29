"""AReaL-RSI command line entry point."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from areal_rsi.controller import LocalRSIController
from areal_rsi.memory import Memory
from areal_rsi.services import MockAReaLService, MockEnvArenaClient, MockResearchAgent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="areal-rsi")
    parser.add_argument("--version", action="store_true", help="Print version and exit.")
    subparsers = parser.add_subparsers(dest="command")

    run_mock = subparsers.add_parser("run-mock", help="Run one local mock RSI iteration.")
    run_mock.add_argument("--ckpt", default="ckpt_000")
    run_mock.add_argument("--task-set", default="swe_verified")
    run_mock.add_argument("--method", default="sft", choices=["sft", "opd"])
    run_mock.add_argument("--harness", default="mock_harness")
    run_mock.add_argument("--run-root", default="runs")

    status = subparsers.add_parser("status", help="Print run state.")
    status.add_argument("run_id")
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
    if args.command == "status":
        controller = LocalRSIController(run_root=args.run_root)
        state = controller.get_status(args.run_id)
        print(json.dumps(state.to_dict(), indent=2, ensure_ascii=False, default=repr))
        return 0
    parser.print_help()
    return 0


def _run_mock(args: argparse.Namespace):
    run_root = Path(args.run_root)
    memory = Memory(run_root / "memory")
    controller = LocalRSIController(
        run_root=run_root,
        areal=MockAReaLService(),
        envarena=MockEnvArenaClient(),
        research_agent=MockResearchAgent(),
        memory=memory,
    )
    return controller.run_iteration(
        ckpt=args.ckpt,
        task_set=args.task_set,
        method=args.method,
        harness_config={"harness": args.harness},
    )


if __name__ == "__main__":
    raise SystemExit(main())
