#!/usr/bin/env python3
"""Apply the local exclusion policy before calling official MLE-bench prepare."""
from pathlib import Path
import json
import sys
import tempfile

from mlebench.cli import main
from mlebench.registry import registry
from task_policy import EXCLUDED_TASKS, require_enabled, selection_parser


def prepare():
    parser = selection_parser()
    parser.add_argument('--dry-run', action='store_true', help='List selected tasks without downloading or preparing data.')
    args, remaining = parser.parse_known_args(sys.argv[1:])
    if args.competition_id:
        require_enabled(args.competition_id)
        identifiers = [args.competition_id]
    elif args.all:
        identifiers = registry.list_competition_ids()
    elif args.lite:
        identifiers = registry.get_lite_competition_ids()
    elif args.list:
        identifiers = Path(args.list).read_text().splitlines()
    else:
        # Delegate upstream help and missing-selection errors unchanged.
        sys.argv = ['mlebench', 'prepare', *remaining]
        return main()
    enabled = [identifier for identifier in identifiers if identifier not in EXCLUDED_TASKS]
    for identifier in identifiers:
        if identifier in EXCLUDED_TASKS:
            print(f"Skipping excluded task: {identifier}: {EXCLUDED_TASKS[identifier]['reason']}", flush=True)
    if not enabled:
        raise SystemExit('No enabled tasks were selected.')
    if args.dry_run:
        print(json.dumps(dict(task_count=len(enabled), tasks=enabled,
                              excluded_tasks=[i for i in identifiers if i in EXCLUDED_TASKS]), indent=2), flush=True)
        return
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt') as selected:
        selected.write('\n'.join(enabled) + '\n')
        selected.flush()
        sys.argv = ['mlebench', 'prepare', '--list', selected.name, *remaining]
        return main()


if __name__ == '__main__':
    prepare()
