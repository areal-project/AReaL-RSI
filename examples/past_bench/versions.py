"""Save separate Harness versions and select one through its manifest file."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from run import candidate_policy, read_json, research_config, write_json


def verify_version(manifest: Path) -> Path:
    record = read_json(manifest)
    source = manifest.resolve().parent / "harness"
    if record.get("format") != "past-bench-harness-v1":
        raise ValueError("Unsupported Harness version manifest")
    _, identity = candidate_policy(source, record["edit_scope"])
    if identity != record["harness_id"]:
        raise ValueError("Saved Harness version changed; save edits as a new version")
    return source


def save_version(source: Path, output: Path, scope: str) -> dict[str, Any]:
    source, output = source.resolve(), output.absolute()
    if output.resolve().is_relative_to(source):
        raise ValueError("Version output must be outside its source")
    _, identity = candidate_policy(source, scope)
    output.mkdir(parents=True, exist_ok=False)
    destination = output / "harness"
    shutil.copytree(source, destination)
    if candidate_policy(destination, scope)[1] != identity or (
        candidate_policy(source, scope)[1] != identity
    ):
        raise ValueError("Harness changed while saving; incomplete output retained")
    record = {"format": "past-bench-harness-v1", "edit_scope": scope, "harness_id": identity}
    write_json(output / "version.json", record)
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    save = commands.add_parser("save")
    save.add_argument("--source", type=Path, required=True)
    save.add_argument("--output", type=Path, required=True)
    save.add_argument("--scope", choices=("prompt", "hermes"), default="hermes")
    for action in ("verify", "configure"):
        command = commands.add_parser(action)
        command.add_argument("--version", type=Path, required=True)
        if action == "configure":
            command.add_argument("--lock", type=Path, required=True)
            command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == "save":
            print(json.dumps(save_version(args.source, args.output, args.scope)))
        else:
            source = verify_version(args.version)
            if args.action == "configure":
                config = research_config(args.lock.resolve(), source)
                write_json(args.output.resolve(), config.public())
            print(json.dumps({"harness": str(source)}))
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(2, f"Harness version unavailable: {error}\n")


if __name__ == "__main__":
    main()
