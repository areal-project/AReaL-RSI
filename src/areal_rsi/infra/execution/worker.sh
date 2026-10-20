#!/usr/bin/env bash
# Runtime setup shared by TIR and Search workers inside an allocated container.
set -euo pipefail
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
export NO_PROXY='*' no_proxy='*'
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export PYTHONDONTWRITEBYTECODE=1
"$1" -c 'from areal_rsi.infra.execution.jobs import verify_gpu_allocation; verify_gpu_allocation()'
cudnn_lib=$("$1" -c \
  'from importlib.metadata import distribution; print(distribution("nvidia-cudnn-cu12").locate_file("nvidia/cudnn/lib"))')
export LD_LIBRARY_PATH="$cudnn_lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$@"
