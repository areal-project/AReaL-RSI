"""Run the calculator/math RSI example."""

import sys

from areal_rsi.examples.tir.profile import PROFILE
from areal_rsi.infra.coordination.generation import cycle_main


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "arena":
        from areal_rsi.examples.tir.arena import main as arena_main

        del sys.argv[1]
        arena_main()
        return
    cycle_main(PROFILE)


if __name__ == "__main__":
    main()
