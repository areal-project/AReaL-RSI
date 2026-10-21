#!/usr/bin/env bash
set -euo pipefail
exec srun --mpi=pmi2 "${MLE_ROOT:?MLE_ROOT must be exported by submit.sh}/scripts/run.sh" "$@"
