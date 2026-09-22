"""Evaluate a submitted graph with trusted engine code and the visible split."""

from __future__ import annotations

import argparse
import json
import sys

sys.path.insert(0, "/rsi/engine")

from swarm import Swarm, evaluate, load_problems


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph", default="/workspace/submission/graph.json")
    args = parser.parse_args()
    swarm = Swarm.load(args.graph)
    swarm.validate()
    print(json.dumps(evaluate(swarm, load_problems("/rsi/val.jsonl")), indent=1))


if __name__ == "__main__":
    main()
