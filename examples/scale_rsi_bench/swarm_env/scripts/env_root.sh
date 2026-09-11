#!/usr/bin/env bash
# Sourced by launchers. Resolve code symlinks but keep runtime assets external.
_rsi_script=$(readlink -f -- "${BASH_SOURCE[0]}")
_rsi_task=$(basename -- "$(dirname -- "$(dirname -- "$_rsi_script")")")
_rsi_base=$(cd -- "$(dirname -- "$_rsi_script")/../../../../.." && pwd)
ROOT=${RSI_ROOT:-${RSI_WORK_ROOT:-$_rsi_base/scale_rsi_bench}/$_rsi_task}
ROOT=$(cd -- "$ROOT" && pwd)
export RSI_ROOT="$ROOT"
unset _rsi_script _rsi_task _rsi_base
