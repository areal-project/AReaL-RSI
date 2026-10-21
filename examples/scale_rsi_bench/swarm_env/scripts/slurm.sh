#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")/env_root.sh"
source "$ROOT/config.sh"
args=(--mpi=pmi2 --nodes=1 --ntasks=1 --gres="gpu:${RSI_GPUS:-$GPUS}"
      --cpus-per-task="${RSI_CPUS:-$CPUS}" --mem="${RSI_MEM:-$MEM}"
      --job-name="wht-rsi-$SHORT" --chdir="$ROOT")
if [[ -n ${RSI_SLURM_JOB_ID:-} ]]; then
  args+=(--jobid="$RSI_SLURM_JOB_ID" --exact)
else
  args+=(--time="${RSI_TIME:-$TIME}")
fi
if [[ $# == 0 ]]; then args+=(--pty); fi
if [[ ${RSI_EXCLUSIVE:-0} == 1 && -z ${RSI_SLURM_JOB_ID:-} ]]; then args+=(--exclusive); fi
if [[ -n ${RSI_EXCLUDE:-} ]]; then args+=(--exclude="$RSI_EXCLUDE"); fi
exec srun "${args[@]}" bash "$ROOT/scripts/container.sh" "$@"
