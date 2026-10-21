#!/usr/bin/env python3
"""Deterministic GPU training test; this is a baseline script, not an Agent."""
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

assert torch.cuda.is_available(), 'CUDA is unavailable'
assert torch.cuda.device_count() == 1, 'Smoke test expects one allocated GPU'
assert not Path('/mlebench-data').exists() or not any(Path('/mlebench-data').iterdir())
assert not Path('/private/data').exists(), 'Private answers must not be mounted'
np.random.seed(0)
torch.manual_seed(0)
torch.set_num_threads(min(int(os.environ.get('OMP_NUM_THREADS', '4')), 8))
train = pd.read_csv('/home/data/train.csv')
test = pd.read_csv('/home/data/test.csv')
assert 'Transported' not in test
columns = ['HomePlanet', 'CryoSleep', 'Destination', 'Age', 'VIP', 'RoomService',
           'FoodCourt', 'ShoppingMall', 'Spa', 'VRDeck']
numeric = ['Age', 'RoomService', 'FoodCourt', 'ShoppingMall', 'Spa', 'VRDeck']
categorical = [c for c in columns if c not in numeric]
features = train[columns].copy()
test_features = test[columns].copy()
for frame in (features, test_features):
    for column in categorical:
        frame[column] = frame[column].fillna('missing').astype(str)
preprocessor = ColumnTransformer([
    ('numeric', make_pipeline(SimpleImputer(strategy='median'), StandardScaler()), numeric),
    ('categorical', OneHotEncoder(handle_unknown='ignore', sparse_output=False), categorical),
])
fit_idx, val_idx = train_test_split(np.arange(len(train)), test_size=.2, random_state=0,
                                  stratify=train.Transported)
preprocessor.fit(features.iloc[fit_idx])
x = torch.tensor(preprocessor.transform(features), dtype=torch.float32, device='cuda')
y = torch.tensor(train.Transported.to_numpy(dtype=np.float32), device='cuda')
model = torch.nn.Sequential(torch.nn.Linear(x.shape[1], 64), torch.nn.ReLU(),
                            torch.nn.Linear(64, 32), torch.nn.ReLU(), torch.nn.Linear(32, 1)).cuda()
optimizer = torch.optim.Adam(model.parameters(), lr=.01)
loss_fn = torch.nn.BCEWithLogitsLoss()
best_score = -1
for epoch in range(120):
    model.train()
    optimizer.zero_grad()
    loss = loss_fn(model(x[fit_idx]).flatten(), y[fit_idx])
    loss.backward()
    optimizer.step()
    model.eval()
    with torch.no_grad():
        score = ((model(x[val_idx]).flatten() >= 0) == y[val_idx].bool()).float().mean().item()
    if score > best_score:
        best_score = score
        best_weights = {k: v.detach().clone() for k, v in model.state_dict().items()}
model.load_state_dict(best_weights)
with torch.no_grad():
    xt = torch.tensor(preprocessor.transform(test_features), dtype=torch.float32, device='cuda')
    predictions = (model(xt).flatten() >= 0).cpu().numpy()
submission = pd.DataFrame({'PassengerId': test.PassengerId, 'Transported': predictions})
submission.to_csv('/home/submission/submission.csv', index=False)
assert best_score > .7, f'Unexpected validation accuracy: {best_score}'
metrics = dict(torch=torch.__version__, cuda=torch.version.cuda,
               gpu=torch.cuda.get_device_name(0), gpu_count=torch.cuda.device_count(),
               validation_accuracy=best_score, train_rows=len(train), test_rows=len(test),
               submission_rows=len(submission))
Path('/home/logs/smoke_metrics.json').write_text(json.dumps(metrics, indent=2) + '\n')
print(json.dumps(metrics, indent=2))
