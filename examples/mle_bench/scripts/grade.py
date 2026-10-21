#!/usr/bin/env python3
import json
import sys
from pathlib import Path

from mlebench.grade import grade_csv
from mlebench.registry import registry
from task_policy import require_enabled

competition_id, run_name = sys.argv[1:]
require_enabled(competition_id)
run = Path('/runs') / run_name
competition = registry.set_data_dir(Path('/mlebench-data')).get_competition(competition_id)
report = grade_csv(run / 'submission/submission.csv', competition)
result = report.to_dict()
(run / 'grading_report.json').write_text(json.dumps(result, indent=2, default=str) + '\n')
print(json.dumps(result, indent=2, default=str))
if not result['valid_submission']:
    raise SystemExit('Submission is invalid')
