#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")/scripts/env_root.sh"
case "${1:-shell}" in
  verify) echo 'OPSD upstream verifier is disabled on this shared cluster: its free_gpus() can kill unrelated processes. Use /workspace/dev_eval.sh for dev evaluation; official five-run verification needs an allocation-scoped adapter.' >&2; exit 2 ;;
  shell) [[ $# == 0 ]] || shift; exec bash "$ROOT/scripts/slurm.sh" "$@" ;;
  smoke) shift; export RSI_GPUS=${RSI_GPUS:-1}; exec bash "$ROOT/scripts/slurm.sh" python "$ROOT/scripts/smoke.py" "$@" ;;
  audit) shift; export RSI_GPUS=${RSI_GPUS:-1}; exec bash "$ROOT/scripts/slurm.sh" python "$ROOT/scripts/check_dependencies.py" "$@" ;;
  *) exec bash "$ROOT/scripts/slurm.sh" "$@" ;;
esac
