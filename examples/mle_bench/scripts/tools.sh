#!/usr/bin/env bash
# Trusted preparation/grading commands, outside the training container's mounts.
set -euo pipefail
source "${MLE_ROOT:-$(dirname -- "$(dirname -- "${BASH_SOURCE[0]}")")}/scripts/env.sh"
mkdir -p "$MLE_ROOT/cache/tools/tmp"
binds=(--bind "$MLE_DATA_DIR:/mlebench-data" --bind "$MLE_ROOT/runs:/runs"
       --bind "$MLE_ROOT/scripts:/mlebench-tools:ro" --bind "$MLE_ROOT/cache/tools:/cache")
if [[ -d ${KAGGLE_CONFIG_DIR:-$HOME/.kaggle} ]]; then
    binds+=(--bind "${KAGGLE_CONFIG_DIR:-$HOME/.kaggle}:/kaggle:ro")
    export APPTAINERENV_KAGGLE_CONFIG_DIR=/kaggle
fi
for var in KAGGLE_USERNAME KAGGLE_KEY; do
    if [[ -n ${!var:-} ]]; then
        export "APPTAINERENV_$var=${!var}"
    fi
done
export APPTAINERENV_OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-4}"
export APPTAINERENV_CUDA_VISIBLE_DEVICES=-1
export APPTAINERENV_TF_NUM_INTRAOP_THREADS="${SLURM_CPUS_PER_TASK:-4}"
export APPTAINERENV_TF_NUM_INTEROP_THREADS=1
export APPTAINERENV_TMPDIR=/cache/tmp
exec apptainer exec --cleanenv --containall --no-home --no-mount hostfs,cwd,bind-paths \
    --writable-tmpfs "${binds[@]}" --pwd /cache "$MLE_IMAGE" "$@"
