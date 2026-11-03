#!/usr/bin/env bash
set -euo pipefail
set +x
umask 077
run_dir=$1
mode=${2:-}
source "$run_dir/environment.sh"
container=(singularity exec --cleanenv --no-eval --pid --writable-tmpfs)
if [[ $mode != --check-only ]]; then container+=(--nv); fi
exec srun --mpi=none --kill-on-bad-exit=1 --nodes=1 --ntasks=1 \
  --cpus-per-task="$SFT_CPUS" \
  "${container[@]}" \
  --bind "$SFT_BINDS" "$SFT_IMAGE" bash "$run_dir/worker.sh" "$run_dir" "$mode"
