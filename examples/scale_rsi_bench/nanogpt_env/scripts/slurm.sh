#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")/env_root.sh"
# Default matches the task's single-GPU resource request; override as needed.
args=(--mpi=pmi2 --nodes=1 --ntasks=1 --gres="gpu:${RSI_GPUS:-1}"
      --cpus-per-task="${RSI_CPUS:-16}" --mem="${RSI_MEM:-64G}"
      --time="${RSI_TIME:-02:00:00}" --job-name=wht-rsi-nanogpt --chdir="$ROOT")
if [[ $# == 0 ]]; then args+=(--pty); fi
exec srun "${args[@]}" bash "$ROOT/scripts/container.sh" "$@"
