"""Run the calculator/math RSI example."""

from areal_rsi.examples.tir.profile import PROFILE
from areal_rsi.infra.coordination.generation import cycle_main


def main() -> None:
    cycle_main(PROFILE)


if __name__ == "__main__":
    main()
