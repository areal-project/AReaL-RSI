"""Compatibility dispatcher for repository workbench and task launchers."""

from __future__ import annotations

import argparse
import runpy
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

from areal_rsi.ui.launch import main as launch_workbench

REPOSITORY = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--type", choices=("web", "demo"), default="web")
    args, remaining = parser.parse_known_args(sys.argv[1:] if argv is None else argv)
    if args.type == "demo":
        launcher = runpy.run_path(str(REPOSITORY / "examples/kernelbench_artifacts/run.py"))
        return cast(Callable[[list[str]], int], launcher["main"])(remaining)
    return launch_workbench(remaining)


if __name__ == "__main__":
    raise SystemExit(main())
