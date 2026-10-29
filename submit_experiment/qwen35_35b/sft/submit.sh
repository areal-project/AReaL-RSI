#!/usr/bin/env bash
set -euo pipefail
exec "${SUBMIT_PYTHON:-python3}" "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/submit.py" "$@"
