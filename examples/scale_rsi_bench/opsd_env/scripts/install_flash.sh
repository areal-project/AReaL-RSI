#!/usr/bin/env bash
set -euo pipefail
ROOT=${RSI_ROOT:?Run inside the OPSD container}
wheel="$ROOT/wheels/flash_attn-2.8.3+cu12torch2.8cxx11abiTRUE-cp312-cp312-linux_x86_64.whl"
printf '%s  %s\n' f25da18657a87fc83dc1bfb8b7751b82246e9db355510226b674fd437c34b5fb "$wheel" | sha256sum -c -
uv pip install --no-cache --reinstall --no-deps --python "$ROOT/.venv/bin/python" "$wheel"
uv pip check --python "$ROOT/.venv/bin/python"
uv pip freeze --python "$ROOT/.venv/bin/python" > "$ROOT/logs/installed-requirements.txt"
