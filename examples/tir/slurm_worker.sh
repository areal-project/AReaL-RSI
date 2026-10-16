#!/usr/bin/env bash
# The host owns Slurm; AReaL's local scheduler owns workers within this allocation.
set -euo pipefail
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
export NO_PROXY='*' no_proxy='*'
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
cudnn_lib=$(/opt/.venv/bin/python -c \
  'from importlib.metadata import distribution; print(distribution("nvidia-cudnn-cu12").locate_file("nvidia/cudnn/lib"))')
export LD_LIBRARY_PATH="$cudnn_lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$@"
