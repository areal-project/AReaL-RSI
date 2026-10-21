#!/usr/bin/env python3
"""Fetch public development input, then run and verify upstream's exact split."""
import argparse
import hashlib
import http.client
import json
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVISION = 'c6b713f78f66af3ee675bbe6e872adfd4449be97'
URL = f'https://huggingface.co/datasets/OPT-Bench/OPT-Bench/resolve/{REVISION}/example_tasks/spaceship-titanic/train.csv'
COMPETITION = 'spaceship-titanic'


def fetch(data_dir):
    raw = data_dir / COMPETITION / 'raw'
    raw.mkdir(parents=True, exist_ok=True)
    total = 805421
    data = bytearray()
    existing = raw / 'train.csv'
    if existing.is_file():
        cached = existing.read_bytes()
        if hashlib.sha1(f'blob {len(cached)}\0'.encode() + cached).hexdigest() == 'cbfd74d7a672da864c81934340b19c7994c0c8f2':
            data.extend(cached)
    failures = 0
    while len(data) < total:
        offset = len(data)
        request = urllib.request.Request(URL, headers={'Range': f'bytes={offset}-{min(offset + 65535, total - 1)}'})
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                try:
                    chunk = response.read()
                except http.client.IncompleteRead as error:
                    chunk = error.partial
                if response.status != 206:
                    chunk = chunk[offset:]
                if not chunk:
                    raise ValueError('Download made no progress')
                data.extend(chunk)
                failures = 0
        except Exception:
            failures += 1
            if failures == 5:
                raise
            time.sleep(2 ** failures)
    data = bytes(data)
    assert len(data) == total
    git_hash = hashlib.sha1(f'blob {total}\0'.encode() + data).hexdigest()
    assert git_hash == 'cbfd74d7a672da864c81934340b19c7994c0c8f2', 'Mirror input checksum mismatch'
    # The prepared files are subsequently checked against upstream checksums.
    if not data.startswith(b'PassengerId,'):
        raise ValueError('Unexpected development dataset format')
    (raw / 'train.csv').write_bytes(data)
    manifest = dict(source=URL, revision=REVISION, raw_sha256=hashlib.sha256(data).hexdigest(),
                    note='Public mirror of original training CSV; official prepared checksums must pass.')
    (data_dir / COMPETITION / 'provenance.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'Downloaded {len(data)} bytes to {raw / "train.csv"}')


def prepare(data_dir):
    import yaml
    from mlebench.registry import registry
    competition = registry.set_data_dir(data_dir).get_competition(COMPETITION)
    competition.public_dir.mkdir(parents=True, exist_ok=True)
    competition.private_dir.mkdir(parents=True, exist_ok=True)
    competition.prepare_fn(raw=competition.raw_dir, public=competition.public_dir,
                           private=competition.private_dir)
    checksums = yaml.safe_load(competition.checksums.read_text())
    verified = {}
    for visibility, folder in [('public', competition.public_dir), ('private', competition.private_dir)]:
        for filename, expected in checksums[visibility].items():
            path = folder / filename
            actual = hashlib.md5(path.read_bytes()).hexdigest()
            if actual != expected:
                raise ValueError(f'Official checksum mismatch for {visibility}/{filename}: {actual} != {expected}')
            verified[f'{visibility}/{filename}'] = actual
    (competition.public_dir / 'description.md').write_text(competition.description)
    manifest_path = data_dir / COMPETITION / 'provenance.json'
    manifest = json.loads(manifest_path.read_text())
    manifest.update(prepared_checksums_verified=True, prepared_md5=verified)
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['fetch', 'prepare'])
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    args = parser.parse_args()
    {'fetch': fetch, 'prepare': prepare}[args.action](args.data_dir)
