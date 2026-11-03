#!/usr/bin/env bash
set -euo pipefail
set +x
run_dir=$1
mode=${2:-}
source "$run_dir/environment.sh"
if [[ -n $CREDENTIALS_FILE ]]; then
  set -a
  source "$CREDENTIALS_FILE"
  set +a
fi
# Credential files may contain stale deployment variables.
source "$run_dir/environment.sh"
export PYTHONPATH=$SFT_PYTHONPATH
if [[ -n ${WANDB_API_KEY:-} && -n ${WANDB_BASE_URL:-} ]]; then
  export WANDB_MODE=online
else
  export WANDB_MODE=disabled
fi
echo "W&B automatic mode: $WANDB_MODE"
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
unset RANK LOCAL_RANK WORLD_SIZE LOCAL_WORLD_SIZE MASTER_ADDR MASTER_PORT GROUP_RANK
cd "$AREAL_DIR"
if [[ $mode == --check-only ]]; then
  exec "$SFT_PYTHON" "$run_dir/train.py" --config "$run_dir/train.yaml" --check-only
fi
exec "$SFT_PYTHON" -m torch.distributed.run --standalone --nnodes=1 --nproc-per-node=8 \
  "$run_dir/train.py" --config "$run_dir/train.yaml"
