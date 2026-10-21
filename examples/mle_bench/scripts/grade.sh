#!/usr/bin/env bash
# grade.sh COMPETITION RUN_NAME; submission must be runs/RUN_NAME/submission/submission.csv.
set -euo pipefail
source "${MLE_ROOT:-$(dirname -- "$(dirname -- "${BASH_SOURCE[0]}")")}/scripts/env.sh"
if [[ ${1:-} == --inside-slurm ]]; then
    shift
    exec srun --mpi=pmi2 "$MLE_ROOT/scripts/tools.sh" python /mlebench-tools/grade.py "$@"
fi
competition=${1:?Usage: grade.sh COMPETITION RUN_NAME}
python "$MLE_ROOT/scripts/task_policy.py" check "$competition"
run=${2:?run name required}
[[ $run != */* && $run != . && $run != .. ]] || { echo 'RUN_NAME must be a directory name under runs/.' >&2; exit 2; }
exec sbatch --parsable --partition="${MLE_PARTITION:-all}" --nodes=1 --ntasks=1 \
    --cpus-per-task="${MLE_GRADE_CPUS:-4}" --mem="${MLE_GRADE_MEM:-16G}" \
    --time="${MLE_GRADE_TIME:-00:30:00}" --job-name=mlebench-grade \
    --output="$MLE_ROOT/logs/grade-%j.log" "$MLE_ROOT/scripts/grade.sh" --inside-slurm "$competition" "$run"
