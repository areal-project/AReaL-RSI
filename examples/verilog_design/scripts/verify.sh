#!/usr/bin/env bash
# Use a CPU allocation; no GPU or model API is needed for the golden checks.
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
runner=${VERILOG_RUNNER_PYTHON:-python3}
export PYTHONPATH="$root/cache/runner-deps${PYTHONPATH:+:$PYTHONPATH}"
bench=${1:?Usage: verify.sh rtllm|verilogeval|cvdp [evaluate.py arguments]}
shift
output="$root/runs/${bench}-golden-${SLURM_JOB_ID:-$(date +%Y%m%d-%H%M%S)}"
if [[ " $* " = *" --comprehension "* ]]; then
    exec apptainer exec --cleanenv --bind "$root:$root" "$root/images/cvdp-v1.1.0-oss.sif" \
        python "$root/scripts/evaluate.py" --bench "$bench" --output "$output" "$@"
fi
exec "$runner" "$root/scripts/evaluate.py" --bench "$bench" --output "$output" \
    --jobs "${SLURM_CPUS_PER_TASK:-4}" "$@"
