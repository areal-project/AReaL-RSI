#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")/env_root.sh"
scratch=$(mktemp -d /tmp/rsi-install-XXXXXXXX)
trap 'rm -r -- "$scratch"' EXIT
mkdir -p "$scratch/tmp" "$scratch/uv"
exec_args=(--no-home --cleanenv --bind /storage:/storage --bind "$scratch:/install-tmp")
singularity exec "${exec_args[@]}" /storage/openpsi/images/areal-dev-sglang-0821.sif \
    bash -c 'set -euo pipefail
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy
root=$1
export PATH="$root/.venv/bin:$PATH" UV_CACHE_DIR=/install-tmp/uv TMPDIR=/install-tmp/tmp UV_HTTP_TIMEOUT=120
uv pip install --link-mode copy --reinstall --index-url https://mirrors.aliyun.com/pypi/simple --python "$root/.venv/bin/python" -r "$root/requirements.txt"
uv pip check --python "$root/.venv/bin/python"
uv pip freeze --python "$root/.venv/bin/python" > "$root/logs/installed-requirements.txt"
' bash "$ROOT"
