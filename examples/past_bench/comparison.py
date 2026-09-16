"""Prepare, but do not start, a fixed 30-round full-Hermes comparison run."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from versions import verify_version

from run import load_lock, preflight, read_json, research_config, write_json


def comparison_config(lock: Path, version: Path) -> dict[str, Any]:
    from areal_rsi.coordination.config import ResearchConfig

    source = verify_version(version)
    record = read_json(version)
    protocol = load_lock(lock)["protocol"]
    if record["edit_scope"] != "hermes" or protocol["edit_scope"] != "hermes":
        raise ValueError("Comparison requires the full Hermes edit scope")
    if protocol["options"]["target_model"] != "gpt-6-astra":
        raise ValueError("Comparison requires the fixed gpt-6-astra Task Agent")
    config: ResearchConfig = research_config(lock, source)
    config.brief.objective = "完成 30 轮 Hermes Harness 优化并独立确认 PAST-Bench 表现"
    config.brief.instructions = Path(__file__).with_name("comparison.md").read_text()
    config.brief.limits.max_nodes = 64
    config.brief.limits.max_model_calls = 4000
    config.brief.limits.max_total_tokens = 50_000_000
    config.brief.limits.max_verify_calls = 38
    config.brief.limits.max_wall_seconds = 172800
    config.commands = ()
    config.model = "gpt-6-astra"
    config.reasoning_effort = "xhigh"
    config.model_settings_pinned = True
    from areal_rsi.execution import ApprovedCommand

    config.preflight = ApprovedCommand(
        "preflight",
        (
            sys.executable,
            str(Path(__file__).resolve()),
            "--lock",
            str(lock),
            "--version",
            str(version),
            "--preflight",
        ),
        timeout_seconds=120,
    )
    config.metadata["comparison"] = {
        "rounds": 30,
        "valid_evaluations": 32,
        "failure_allowance": 6,
        "seed_harness_id": record["harness_id"],
        "seed_version": str(version),
        "enforcement": "agent instructions plus aggregate framework budgets; audit the ledger",
    }
    return config.public()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--version", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    try:
        config = comparison_config(args.lock.resolve(), args.version.resolve())
        if args.preflight:
            import json

            print(json.dumps(preflight(args.lock.resolve())))
        elif args.output is not None:
            write_json(args.output.resolve(), config)
            print(f"Comparison configuration written: {args.output}")
        else:
            parser.error("--output is required when not running --preflight")
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(2, f"Comparison configuration unavailable: {error}\n")


if __name__ == "__main__":
    main()
