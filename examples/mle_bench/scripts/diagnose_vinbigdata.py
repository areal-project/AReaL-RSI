#!/usr/bin/env python3
"""Isolate the pinned upstream VinBigData zero-based annotation-ID behavior.

The in-memory ID change below is diagnostic only. No benchmark code is patched.
"""
import argparse
import importlib
import json
from pathlib import Path

from mlebench.registry import registry
from audit_fixtures import fixtures

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
comp = registry.get_competition('vinbigdata-chest-xray-abnormalities-detection')
module = importlib.import_module(comp.grader.grade_fn.__module__)
submission, answers, _ = fixtures(comp, module)
baseline = float(comp.grader.grade_fn(submission.copy(), answers.copy()))
evaluator = module.VinBigDataEval(answers.copy())
original_ids = [annotation['id'] for annotation in evaluator.annotations['annotations']]
for annotation in evaluator.annotations['annotations']:
    annotation['id'] += 1
diagnostic = float(evaluator.evaluate(submission.copy()).stats[0])
report = dict(task=comp.id, original_annotation_ids=original_ids, baseline_perfect_prediction_score=baseline,
              expected_perfect_prediction_score=1., diagnostic_one_based_annotation_score=diagnostic,
              note='Annotation IDs were changed in a diagnostic object only; official grader and SIF are unchanged.')
args.output.write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2), flush=True)
