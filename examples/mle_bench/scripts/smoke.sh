#!/usr/bin/env bash
# Submit the full prepare -> GPU train -> CPU grade dependency chain.
set -euo pipefail
source "${MLE_ROOT:-$(dirname -- "$(dirname -- "${BASH_SOURCE[0]}")")}/scripts/env.sh"
if [[ ${1:-} == --prepare-inside-slurm ]]; then
    exec srun --mpi=pmi2 "$MLE_ROOT/scripts/tools.sh" python /mlebench-tools/prepare_dev.py prepare --data-dir /mlebench-data
fi
python3 "$MLE_ROOT/scripts/prepare_dev.py" fetch --data-dir "$MLE_DATA_DIR"
run="smoke-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$MLE_ROOT/runs/$run"
prep=$(sbatch --parsable --partition="${MLE_PARTITION:-all}" --nodes=1 --ntasks=1 \
    --cpus-per-task=4 --mem=16G --time=00:10:00 --job-name=mleb-smoke-prepare \
    --output="$MLE_ROOT/logs/smoke-prepare-%j.log" "$MLE_ROOT/scripts/smoke.sh" --prepare-inside-slurm)
train=$(sbatch --parsable --dependency="afterok:$prep" --kill-on-invalid-dep=yes --partition="${MLE_PARTITION:-all}" \
    --nodes=1 --ntasks=1 --gres=gpu:1 --cpus-per-task=4 --mem=16G --time=00:10:00 \
    --job-name=mleb-smoke-train --output="$MLE_ROOT/logs/smoke-train-%j.log" \
    "$MLE_ROOT/scripts/task_job.sh" spaceship-titanic "$MLE_ROOT/runs/$run" python /mlebench-tools/smoke_train.py)
grade=$(sbatch --parsable --dependency="afterok:$train" --kill-on-invalid-dep=yes --partition="${MLE_PARTITION:-all}" \
    --nodes=1 --ntasks=1 --cpus-per-task=4 --mem=16G --time=00:10:00 \
    --job-name=mleb-smoke-grade --output="$MLE_ROOT/logs/smoke-grade-%j.log" \
    "$MLE_ROOT/scripts/grade.sh" --inside-slurm spaceship-titanic "$run")
python3 - "$MLE_ROOT/runs/$run" "$prep" "$train" "$grade" <<'PY'
import json, sys
from pathlib import Path
run, prep, train, grade = sys.argv[1:]
report = dict(run_dir=run, prepare_job=prep, train_job=train, grade_job=grade)
Path(run, 'jobs.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
PY
