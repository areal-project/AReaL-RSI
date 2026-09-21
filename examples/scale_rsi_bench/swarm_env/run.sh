#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")/scripts/env_root.sh"
server_session="$ROOT/scripts/server_session.py"
if [[ ${RSI_RESTRICTED:-0} == 1 ]]; then server_session=/rsi/scripts/server_session.py; fi
case "${1:-shell}" in
  serve) shift; exec bash "$ROOT/scripts/slurm.sh" python "$server_session" "$@" ;;
  shell) [[ $# == 0 ]] || shift; exec bash "$ROOT/scripts/slurm.sh" "$@" ;;
  smoke) shift; export RSI_GPUS=${RSI_GPUS:-1}; exec bash "$ROOT/scripts/slurm.sh" python "$ROOT/scripts/smoke.py" "$@" ;;
  audit) shift; export RSI_GPUS=${RSI_GPUS:-1}; exec bash "$ROOT/scripts/slurm.sh" python "$ROOT/scripts/check_dependencies.py" "$@" ;;
  *) exec bash "$ROOT/scripts/slurm.sh" "$@" ;;
esac
