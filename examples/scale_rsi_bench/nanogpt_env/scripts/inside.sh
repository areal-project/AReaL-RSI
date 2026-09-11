#!/usr/bin/env bash
set -euo pipefail
# The base image contains a proxy pointing at an unavailable local endpoint.
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy
if [[ ${1:-} == install ]]; then
    if [[ ! -x "$RSI_ROOT/.venv/bin/python" ]]; then
        uv venv --python /usr/bin/python3 "$RSI_ROOT/.venv"
    fi
    UV_HTTP_TIMEOUT=120 uv pip install --index-url https://mirrors.aliyun.com/pypi/simple \
        --python "$RSI_ROOT/.venv/bin/python" -r "$RSI_ROOT/requirements-nanogpt.txt"
    uv pip check --python "$RSI_ROOT/.venv/bin/python"
    uv pip freeze --python "$RSI_ROOT/.venv/bin/python" > "$RSI_ROOT/logs/installed-requirements.txt"
    exit 0
fi
source "$RSI_ROOT/.venv/bin/activate"
export HF_HUB_OFFLINE=${HF_HUB_OFFLINE:-1}
export TRANSFORMERS_OFFLINE=${TRANSFORMERS_OFFLINE:-1}
export OMP_NUM_THREADS=${OMP_NUM_THREADS:-8}
if [[ $# == 0 ]]; then
    exec bash --norc -i
fi
exec "$@"
