#!/usr/bin/env bash
set -euo pipefail
ROOT=${1:?environment root required}
RUN=${2:?experiment directory required}
exec srun --mpi=pmi2 bash "$ROOT/scripts/container.sh" \
    python -u "$ROOT/scripts/run_experiment.py" "$RUN"
