#!/usr/bin/env bash
set -euo pipefail
source "${MLE_ROOT:-$(dirname -- "$(dirname -- "${BASH_SOURCE[0]}")")}/scripts/env.sh"
if [[ ${1:-} == --inside-slurm ]]; then
    shift
    exec srun --mpi=pmi2 "$MLE_ROOT/scripts/tools.sh" python /mlebench-tools/prepare_selected.py --data-dir /mlebench-data "$@"
fi
if [[ $# -gt 0 ]]; then
    python "$MLE_ROOT/scripts/task_policy.py" check-prepare "$@"
fi
exec sbatch --parsable --partition="${MLE_PARTITION:-all}" --nodes=1 --ntasks=1 \
    --cpus-per-task="${MLE_PREP_CPUS:-8}" --mem="${MLE_PREP_MEM:-64G}" \
    --time="${MLE_PREP_TIME:-24:00:00}" --job-name=mlebench-prepare \
    --output="$MLE_ROOT/logs/prepare-%j.log" "$MLE_ROOT/scripts/prepare.sh" --inside-slurm "$@"
