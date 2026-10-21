#!/usr/bin/env python3
"""Shared local task exclusions; standard-library only, also runs on the host."""
import argparse
import json
from pathlib import Path

POLICY = json.loads(Path(__file__).with_name('task_policy.json').read_text())
EXCLUDED_TASKS = POLICY['excluded_tasks']


def require_enabled(identifier):
    if identifier in EXCLUDED_TASKS:
        raise SystemExit(f"Excluded task: {identifier}. {EXCLUDED_TASKS[identifier]['reason']}")


def selection_parser():
    parser = argparse.ArgumentParser(add_help=False)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('-c', '--competition-id')
    group.add_argument('-a', '--all', action='store_true')
    group.add_argument('--lite', action='store_true')
    group.add_argument('-l', '--list')
    return parser


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 3:
        raise SystemExit('Usage: task_policy.py check TASK | check-prepare PREPARE_ARGS...')
    if sys.argv[1] == 'check':
        require_enabled(sys.argv[2])
    elif sys.argv[1] == 'check-prepare':
        args, _ = selection_parser().parse_known_args(sys.argv[2:])
        if args.competition_id:
            require_enabled(args.competition_id)
    else:
        raise SystemExit(f'Unknown command: {sys.argv[1]}')
