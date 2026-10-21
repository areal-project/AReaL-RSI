#!/usr/bin/env bash
# submit.sh COMPETITION WORKDIR COMMAND [ARGS...]; prints only the Slurm job ID.
set -euo pipefail
source "${MLE_ROOT:-$(dirname -- "$(dirname -- "${BASH_SOURCE[0]}")")}/scripts/env.sh"
competition=${1:?Usage: submit.sh COMPETITION WORKDIR COMMAND [ARGS...]}
python "$MLE_ROOT/scripts/task_policy.py" check "$competition"
workdir=${2:?work directory required}
shift 2
[[ $# -gt 0 ]] || { echo 'A command is required.' >&2; exit 2; }
mkdir -p "$workdir"
workdir=$(cd "$workdir" && pwd -P)
exec sbatch --parsable --partition="${MLE_PARTITION:-all}" --nodes=1 --ntasks=1 \
    --gres="gpu:${MLE_GPUS:-1}" --cpus-per-task="${MLE_CPUS:-16}" \
    --mem="${MLE_MEM:-128G}" --time="${MLE_TIME:-24:00:00}" \
    --job-name="mleb-$competition" --output="$MLE_ROOT/logs/train-%j.log" \
    "$MLE_ROOT/scripts/task_job.sh" "$competition" "$workdir" "$@"
