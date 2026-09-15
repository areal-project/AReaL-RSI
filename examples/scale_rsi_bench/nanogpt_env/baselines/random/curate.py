"""Generate a deterministic random ordering of the fixed corpus document IDs."""
import argparse
import json
from pathlib import Path
import random


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("selection.json"))
    args = parser.parse_args()
    selection = list(range(182_016))
    random.Random(1337).shuffle(selection)
    args.output.write_text(json.dumps(selection, indent=2) + "\n")
    print(f"Wrote {len(selection)} document IDs to {args.output}")


if __name__ == "__main__":
    main()
