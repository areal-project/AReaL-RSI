#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")/env_root.sh"
cd "$ROOT"
PIN=3061df5c8fa466830fca8c82fe2b7a1f14708b62
if [[ ! -d source/.git ]]; then
    env -u HTTPS_PROXY -u HTTP_PROXY -u ALL_PROXY -u https_proxy -u http_proxy -u all_proxy \
        git clone --filter=blob:none --sparse https://github.com/scaleapi/rsi-benchmark.git source
    git -C source checkout "$PIN"
    env -u HTTPS_PROXY -u HTTP_PROXY -u ALL_PROXY -u https_proxy -u http_proxy -u all_proxy \
        git -C source sparse-checkout set tasks/nano-gpt-data-curation
fi
[[ $(git -C source rev-parse HEAD) == "$PIN" ]] || { echo 'Unexpected source revision' >&2; exit 1; }
TASK="$ROOT/source/tasks/nano-gpt-data-curation"
mkdir -p workspace/{data,submission} logs pool
# Copy upstream assets once; never overwrite an existing working file.
cp -n "$TASK"/environment/workspace/* workspace/
cp -n "$TASK/instruction.md" workspace/
cp -n "$TASK/environment/assets/multi_dev.npy" workspace/data/
bash scripts/slurm.sh install
if [[ ! -f pool/pool_tokens.npy || ! -f pool/pool_meta.npy || ! -f pool/pool.jsonl || ! -f pool/pool_tokens.sha256 || ! -d cache/huggingface/hub/models--gpt2 ]]; then
    env -u HTTPS_PROXY -u HTTP_PROXY -u ALL_PROXY -u https_proxy -u http_proxy -u all_proxy \
        HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}" python scripts/download_data.py
fi
bash scripts/slurm.sh python "$ROOT/scripts/build_data.py"
bash scripts/slurm.sh python "$ROOT/scripts/smoke.py"
