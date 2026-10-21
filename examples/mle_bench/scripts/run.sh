#!/usr/bin/env bash
# Run a training command inside an existing Slurm GPU allocation.
set -euo pipefail
source "${MLE_ROOT:-$(dirname -- "$(dirname -- "${BASH_SOURCE[0]}")")}/scripts/env.sh"
competition=${1:?Usage: run.sh COMPETITION WORKDIR COMMAND [ARGS...]}
python "$MLE_ROOT/scripts/task_policy.py" check "$competition"
workdir=${2:?work directory required}
shift 2
[[ $# -gt 0 ]] || { echo 'A command is required.' >&2; exit 2; }
[[ -n ${SLURM_JOB_ID:-} && -n ${CUDA_VISIBLE_DEVICES:-} ]] || {
    echo 'Launch inside a Slurm GPU allocation, or use scripts/submit.sh.' >&2; exit 2;
}
public="$MLE_DATA_DIR/$competition/prepared/public"
[[ -f "$public/description.md" ]] || { echo "Task not prepared: $public" >&2; exit 2; }
mkdir -p "$workdir"/{submission,logs,.cache,.tmp}
workdir=$(cd "$workdir" && pwd -P)
export APPTAINERENV_CUDA_VISIBLE_DEVICES="$CUDA_VISIBLE_DEVICES"
export APPTAINERENV_OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-16}"
export APPTAINERENV_COMPETITION_ID="$competition"
export APPTAINERENV_HF_HOME=/cache/huggingface
export APPTAINERENV_TORCH_HOME=/cache/torch
export APPTAINERENV_PIP_CACHE_DIR=/cache/pip
export APPTAINERENV_XDG_CACHE_HOME=/cache
export APPTAINERENV_KERAS_HOME=/cache/keras
export APPTAINERENV_TRITON_CACHE_DIR=/cache/triton
export APPTAINERENV_TORCHINDUCTOR_CACHE_DIR=/cache/torchinductor
export APPTAINERENV_TMPDIR=/workspace/.tmp
exec apptainer exec --nv --containall --cleanenv --no-home \
    --no-mount hostfs,cwd,bind-paths --writable-tmpfs \
    --bind "$public:/home/data:ro" --bind "$workdir:/workspace" \
    --bind "$workdir/submission:/home/submission" --bind "$workdir/logs:/home/logs" \
    --bind "$workdir/.cache:/cache" --bind "$MLE_ROOT/scripts:/mlebench-tools:ro" \
    --pwd /workspace "$MLE_IMAGE" "$@"
