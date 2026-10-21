#!/usr/bin/env python3
"""Offline per-task metadata, dependency and actual synthetic metric audit.

Does not download data, train competition models, or claim benchmark scores.
"""
import argparse
import ast
from collections import Counter
from contextlib import nullcontext, redirect_stderr, redirect_stdout
import csv
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import shutil
import time
import traceback
from unittest.mock import patch

import numpy as np
import pandas as pd
import yaml

from mlebench.registry import registry
from audit_fixtures import fixtures
from task_policy import EXCLUDED_TASKS, POLICY


def digest(path):
    result = hashlib.md5()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, default=Path('/mlebench-data'))
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    log_dir = args.output.parent / 'task-logs'
    log_dir.mkdir(exist_ok=True)
    reg = registry.set_data_dir(args.data_dir)
    splits = {}
    for name in ['low', 'medium', 'high', 'dev']:
        for identifier in (reg.get_splits_dir() / f'{name}.txt').read_text().splitlines():
            splits[identifier] = name
    results = []
    for identifier in reg.list_competition_ids():
        if identifier in EXCLUDED_TASKS:
            print(f'Excluded by local policy: {identifier}', flush=True)
            continue
        started = time.monotonic()
        row = dict(id=identifier, split=splits.get(identifier, 'unknown'),
                   registry='not_checked', metadata='not_checked',
                   preparation_dependencies='not_checked', synthetic_grading='not_checked',
                   real_data='not_present', real_preparation='not_run', training='not_run',
                   log=f'task-logs/{identifier}.log')
        with (log_dir / f'{identifier}.log').open('w') as log, redirect_stdout(log), redirect_stderr(log):
            try:
                comp = reg.get_competition(identifier)
                row['registry'] = 'passed'
                assert callable(comp.prepare_fn) and callable(comp.grader.grade_fn)
                assert comp.description.strip()
                assert not comp.leaderboard.read_bytes().startswith(b'version https://git-lfs')
                leaderboard = pd.read_csv(comp.leaderboard)
                assert len(leaderboard) and pd.to_numeric(leaderboard['score']).notna().all()
                checksums = yaml.safe_load(comp.checksums.read_text())
                assert isinstance(checksums['public'], dict) and isinstance(checksums['private'], dict)
                row.update(metadata='passed', leaderboard_rows=len(leaderboard))

                # Include imports inside functions; import availability is a static check.
                tree = ast.parse(inspect.getsource(importlib.import_module(comp.prepare_fn.__module__)))
                packages = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        packages.update(alias.name.split('.')[0] for alias in node.names)
                    elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                        packages.add(node.module.split('.')[0])
                missing = sorted(p for p in packages if importlib.util.find_spec(p) is None)
                row.update(preparation_packages=sorted(packages), missing_packages=missing,
                           preparation_dependencies='failed' if missing else 'passed')

                expected_files = [(args.data_dir / identifier / 'prepared' / group / name, checksum)
                                  for group in ['public', 'private']
                                  for name, checksum in checksums[group].items()]
                existing = [str(p.relative_to(args.data_dir)) for p, _ in expected_files if p.is_file()]
                row['prepared_files_present'] = len(existing)
                row['prepared_files_expected'] = len(expected_files)
                if existing:
                    mismatched = [str(p.relative_to(args.data_dir)) for p, checksum in expected_files
                                  if p.is_file() and digest(p) != checksum]
                    row['checksum_mismatches'] = mismatched
                    row['real_data'] = ('checksum_verified' if len(existing) == len(expected_files)
                                        and not mismatched else 'partial_or_mismatched')

                module = importlib.import_module(comp.grader.grade_fn.__module__)
                submission, answers, expected = fixtures(comp, module)
                # Upstream 3-D metric starts cpu_count() workers (128 on this host).
                # Bound only the worker count for this tiny fixture; metric logic is unchanged.
                context = patch('multiprocessing.cpu_count', return_value=2) if identifier.startswith('3d-') else nullcontext()
                with context:
                    value = float(comp.grader.grade_fn(submission, answers))
                row.update(synthetic_score=value, expected_synthetic_score=expected)
                assert np.isfinite(value), f'Nonfinite metric: {value}'
                assert expected is None or np.isclose(value, expected, atol=1e-5), (value, expected)
                ranking = comp.grader.rank_score(round(value, 5), leaderboard)
                assert all(np.isfinite(ranking[k]) for k in ['gold_threshold', 'silver_threshold',
                                                            'bronze_threshold', 'median_threshold'])
                row.update(synthetic_grading='passed', synthetic_score=value,
                           expected_synthetic_score=expected, ranking='passed')
            except Exception as error:
                row['error'] = f'{type(error).__name__}: {error}'
                if row['registry'] == 'not_checked':
                    row['registry'] = 'failed'
                elif row['metadata'] == 'not_checked':
                    row['metadata'] = 'failed'
                if row['registry'] == 'passed' and row['metadata'] == 'passed':
                    row['synthetic_grading'] = 'failed'
                traceback.print_exc()
        row['seconds'] = round(time.monotonic() - started, 3)
        results.append(row)
        print(f"{identifier}: registry={row['registry']} metric={row['synthetic_grading']} "
              f"data={row['real_data']} {row.get('error', '')}", flush=True)

    summary = {key: dict(Counter(row[key] for row in results)) for key in
               ['split', 'registry', 'metadata', 'preparation_dependencies', 'synthetic_grading', 'real_data']}
    report = dict(created_at=datetime.now(timezone.utc).isoformat(), slurm_job=os.environ.get('SLURM_JOB_ID'),
                  upstream_task_count=len(reg.list_competition_ids()),
                  excluded_tasks=EXCLUDED_TASKS, runtime_limitations=POLICY['runtime_limitations'],
                  scope='Offline synthetic grading and static preparation dependencies; no competition training.',
                  limitations=['Real preparation and real-data training were not run by this audit.',
                               'Synthetic metric scores and ranking checks are not benchmark scores or medals.',
                               'No pretrained weights, network downloads, or arbitrary Agent-selected packages were tested.',
                               '3-D grader worker count was bounded to 2; its numerical metric code was unchanged.'],
                  executables={p: shutil.which(p) for p in ['python', 'git', 'ffmpeg', 'unzip', 'tar']},
                  summary=summary, tasks=results)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    fields = ['id', 'split', 'registry', 'metadata', 'preparation_dependencies', 'synthetic_grading',
              'synthetic_score', 'real_data', 'prepared_files_present', 'prepared_files_expected',
              'real_preparation', 'training', 'error', 'log']
    with args.output.with_suffix('.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(results)
    print(json.dumps(summary, indent=2), flush=True)
    # Exit status reflects findings, while JSON remains available even on failure.
    raise SystemExit(int(any(r['synthetic_grading'] != 'passed' or r['registry'] != 'passed'
                             or r['preparation_dependencies'] != 'passed' for r in results)))


if __name__ == '__main__':
    main()
