#!/usr/bin/env bash
# Start the Search controller with this checkout's source and installed Python.
set -euo pipefail
umask 077

search_workspace=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
search_python=${RSI_PYTHON:-"$search_workspace/.venv/bin/python"}
if ! search_python=$(command -v -- "$search_python"); then
    printf '%s\n' \
        'Search requires an installed Python with the RSI dependencies.' \
        "Set RSI_PYTHON to its executable; the default is this checkout's .venv/bin/python." >&2
    exit 1
fi

export PYTHONPATH="$search_workspace/src:$search_workspace${PYTHONPATH:+:$PYTHONPATH}"
exec "$search_python" "$search_workspace/examples/search/goal.py" \
    --workspace "$search_workspace" "$@"
