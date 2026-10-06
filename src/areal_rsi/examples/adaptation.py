"""Select the shared adaptation Bench recipe."""

import sys

from areal_rsi.scripts.train import main as train_main


def main() -> None:
    train_main(["--recipe", "areal_rsi.infra.coordination.evaluation.bench.prepare", *sys.argv[1:]])


if __name__ == "__main__":
    main()
