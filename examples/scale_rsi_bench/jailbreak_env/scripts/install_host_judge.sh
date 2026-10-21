#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")/env_root.sh"
venv="$ROOT/.judge-venv"
if [[ ! -x "$venv/bin/python" ]]; then
    uv venv --python 3.12 "$venv"
fi
unset all_proxy ALL_PROXY
uv pip install --python "$venv/bin/python" \
    'openai>=1.50' 'langdetect==1.0.9' 'nltk==3.9.1' \
    'immutabledict==4.2.0' 'absl-py==2.1.0'
"$venv/bin/python" -c 'import absl, immutabledict, langdetect, nltk, openai'
