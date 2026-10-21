#!/usr/bin/env python3
"""Check registry/grader imports for every packaged task, without datasets/GPU."""
import importlib
import json
from pathlib import Path

from mlebench.registry import registry

for name in ['torch', 'torchvision', 'torchaudio', 'numpy', 'pandas', 'scipy', 'sklearn',
             'xgboost', 'lightgbm', 'catboost', 'tensorflow', 'transformers', 'datasets',
             'timm', 'albumentations', 'segmentation_models_pytorch', 'librosa']:
    module = importlib.import_module(name)
    print(f'{name}: {getattr(module, "__version__", "import OK")}', flush=True)
ids = registry.list_competition_ids()
failures = {}
for competition_id in ids:
    try:
        competition = registry.get_competition(competition_id)
        assert competition.leaderboard.read_bytes()[:40].find(b'git-lfs') == -1
    except Exception as error:
        failures[competition_id] = f'{type(error).__name__}: {error}'
print(json.dumps(dict(competitions=len(ids), failures=failures), indent=2), flush=True)
assert len(ids) >= 75
assert not failures, 'Some task preparation/grading modules failed to load'
assert not Path('/opt/mle-bench/agents').exists()
assert not list(Path('/opt/mle-bench/mlebench/competitions').glob('*/top_solutions'))
