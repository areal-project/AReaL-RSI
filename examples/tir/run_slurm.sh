#!/usr/bin/env bash
# Submit one allocation; all AReaL workers remain inside it via LocalScheduler.
set -euo pipefail
if [[ ${1:-} == --inside ]]; then
  project=$2
  exec srun --mpi=none apptainer exec --nv --bind "$TIR_BINDS" \
    "$4" bash "$project/examples/tir/run_slurm.sh" --container "$3"
fi
project=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
if [[ ${1:-} == --container ]]; then
  unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
  export NO_PROXY='*' no_proxy='*'
  export PYTHONPATH="$project/src:$project${PYTHONPATH:+:$PYTHONPATH}"
  gpu_python=${TIR_PYTHON:-/opt/.venv/bin/python}
  cudnn_lib=$("$gpu_python" -c \
    'from importlib.metadata import distribution; print(distribution("nvidia-cudnn-cu12").locate_file("nvidia/cudnn/lib"))')
  export LD_LIBRARY_PATH="$cudnn_lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  exec "$gpu_python" -m examples.tir.pipeline --config "$2"
fi
config=${1:-$project/examples/tir/config.json}
config=$(realpath "$config")
: "${TIR_IMAGE:?Set TIR_IMAGE to an existing Apptainer image}"
: "${TIR_BINDS:?Set TIR_BINDS to comma-separated host:container mounts}"
image=$TIR_IMAGE
[[ -f "$image" ]] || { echo "Image not found: $image" >&2; exit 1; }
export TIR_BINDS TIR_IMAGE
resources=(--parsable)
[[ -z ${TIR_RESERVATION:-} ]] || resources+=("--reservation=$TIR_RESERVATION")
[[ -z ${TIR_PARTITION:-} ]] || resources+=("--partition=$TIR_PARTITION")
run_root=${TIR_SUBMIT_ROOT:-$project/runs/tir-slurm}
mkdir -p "$run_root"
sbatch "${resources[@]}" \
  --nodes=1 --ntasks=1 --gres=gpu:2 --cpus-per-task=16 --mem=128G \
  --time="${TIR_TIME_LIMIT:-01:30:00}" --job-name=rsi-tir \
  --output="$run_root/slurm-%j.log" --chdir="$project" \
  "$project/examples/tir/run_slurm.sh" --inside "$project" "$config" "$image"
