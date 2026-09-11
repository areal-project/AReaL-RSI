#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")/env_root.sh"
source "$ROOT/config.sh"
args=(--mpi=pmi2 --nodes=1 --ntasks=1 --gres="gpu:${RSI_GPUS:-$GPUS}"
      --cpus-per-task="${RSI_CPUS:-$CPUS}" --mem="${RSI_MEM:-$MEM}"
      --time="${RSI_TIME:-$TIME}" --job-name="wht-rsi-$SHORT" --chdir="$ROOT")
if [[ $# == 0 ]]; then args+=(--pty); fi
if [[ ${RSI_EXCLUSIVE:-0} == 1 ]]; then args+=(--exclusive); fi
if [[ -n ${RSI_EXCLUDE:-} ]]; then args+=(--exclude="$RSI_EXCLUDE"); fi
exec srun "${args[@]}" bash "$ROOT/scripts/container.sh" "$@"
