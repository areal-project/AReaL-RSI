#!/usr/bin/env bash
# audit.sh [RUN_NAME] submits independent CPU task and single-GPU runtime audits.
set -euo pipefail
source "${MLE_ROOT:-$(dirname -- "$(dirname -- "${BASH_SOURCE[0]}")")}/scripts/env.sh"
if [[ ${1:-} == --inside-cpu ]]; then
    shift
    exec srun --mpi=pmi2 "$MLE_ROOT/scripts/tools.sh" python /mlebench-tools/audit_tasks.py \
        --output "/runs/$1/task-audit.json"
fi
run_name=${1:-environment-audit-$(date +%Y%m%d-%H%M%S)}
[[ $run_name =~ ^[a-zA-Z0-9_-]+$ ]] || { echo 'RUN_NAME must be a simple directory name.' >&2; exit 2; }
run="$MLE_ROOT/runs/$run_name"
mkdir -p "$run"
cpu_job=$(sbatch --parsable --partition="${MLE_PARTITION:-all}" --nodes=1 --ntasks=1 \
    --cpus-per-task=4 --mem=16G --time=00:30:00 --job-name=mleb-audit-cpu \
    --output="$MLE_ROOT/logs/audit-cpu-%j.log" "$MLE_ROOT/scripts/audit.sh" --inside-cpu "$run_name")
if ! gpu_job=$(MLE_GPUS=1 MLE_CPUS=8 MLE_MEM=32G MLE_TIME=00:30:00 "$MLE_ROOT/scripts/submit.sh" \
    spaceship-titanic "$run" python /mlebench-tools/audit_runtime.py --output /workspace/runtime-audit.json); then
    echo "CPU audit already submitted: $cpu_job; GPU submission failed." >&2
    exit 1
fi
printf '{"cpu_job":"%s","gpu_job":"%s","run_name":"%s"}\n' "$cpu_job" "$gpu_job" "$run_name" | tee "$run/audit-jobs.json"
echo "Reports: $run/{task-audit.json,task-audit.csv,runtime-audit.json}"
