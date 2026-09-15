#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")/env_root.sh"
IMAGE=${RSI_IMAGE:-/storage/openpsi/images/areal-dev-sglang-0821.sif}
mkdir -p "$ROOT"/{workspace,cache/huggingface,cache/uv,cache/pip,cache/triton,cache/torch,logs,pool}
extra_binds=(--bind "$ROOT/pool:/opt/pool:ro")
if [[ ${1:-} == verify ]]; then
    shift
    mkdir -p "$ROOT/results"
    run=$(mktemp -d "$ROOT/results/verifier-XXXXXXXX")
    mkdir -p "$run/tests" "$run/logs"
    cp "$ROOT/source/tasks/nano-gpt-data-curation/tests/"* "$run/tests/"
    extra_binds+=(--bind "$run/tests:/tests" --bind "$run/logs:/logs")
    echo "Verifier outputs: $run/logs/verifier"
    set -- bash /tests/test.sh "$@"
fi
export SINGULARITYENV_RSI_ROOT="$ROOT"
export SINGULARITYENV_HF_HOME="$ROOT/cache/huggingface"
export SINGULARITYENV_UV_CACHE_DIR="$ROOT/cache/uv"
export SINGULARITYENV_PIP_CACHE_DIR="$ROOT/cache/pip"
export SINGULARITYENV_TRITON_CACHE_DIR="$ROOT/cache/triton"
export SINGULARITYENV_TORCHINDUCTOR_CACHE_DIR="$ROOT/cache/torch"
export SINGULARITYENV_PYTHONNOUSERSITE=1
export SINGULARITYENV_TOKENIZERS_PARALLELISM=false
export SINGULARITYENV_OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-8}"
if [[ -n ${CUDA_VISIBLE_DEVICES:-} ]]; then
    export SINGULARITYENV_CUDA_VISIBLE_DEVICES="$CUDA_VISIBLE_DEVICES"
elif [[ ${1:-} != install ]]; then
    echo 'CUDA_VISIBLE_DEVICES is unset; launch through nanogpt_env/scripts/slurm.sh or an allocated GPU job.' >&2
    exit 1
fi
exec singularity exec --nv --no-home --cleanenv --contain --writable-tmpfs \
    --bind /storage:/storage --bind "$ROOT/workspace:/workspace" \
    "${extra_binds[@]}" --pwd /workspace "$IMAGE" \
    bash "$ROOT/scripts/inside.sh" "$@"
