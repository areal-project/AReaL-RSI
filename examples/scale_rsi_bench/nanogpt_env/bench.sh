#!/usr/bin/env bash
# Run from any directory; Slurm submission is asynchronous.
set -euo pipefail
source "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")/scripts/env_root.sh"
exec python3 "$ROOT/scripts/bench.py" "$@"
