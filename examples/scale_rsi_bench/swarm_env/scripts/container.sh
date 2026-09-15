#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")/env_root.sh"
source "$ROOT/config.sh"
mkdir -p "$ROOT"/{workspace,models,assets,eval_src,nltk_data,cache/huggingface,cache/uv,cache/torch,cache/triton,logs}
export SINGULARITYENV_RSI_ROOT="$ROOT"
export SINGULARITYENV_HF_HOME=/opt/hf_cache
export SINGULARITYENV_UV_CACHE_DIR="$ROOT/cache/uv"
export SINGULARITYENV_TORCHINDUCTOR_CACHE_DIR="$ROOT/cache/torch"
export SINGULARITYENV_TRITON_CACHE_DIR="$ROOT/cache/triton"
export SINGULARITYENV_NLTK_DATA=/opt/nltk_data
export SINGULARITYENV_PYTHONNOUSERSITE=1
export SINGULARITYENV_OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-8}"
export SINGULARITYENV_CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-}"
# Forward grader credentials only when explicitly present; never log their values.
for name in OPENAI_API_KEY OPENAI_BASE_URL; do
    if [[ -n ${!name:-} ]]; then export "SINGULARITYENV_${name}=${!name}"; fi
done
binds=(--bind /storage:/storage --bind "$ROOT/workspace:/workspace"
       --bind "$ROOT/models:/opt/models:ro" --bind "$ROOT/assets:/opt/eval_assets"
       --bind "$ROOT/eval_src:/opt/eval_src" --bind "$ROOT/nltk_data:/opt/nltk_data"
       --bind "$ROOT/cache/huggingface:/opt/hf_cache")
if [[ ${1:-} == verify ]]; then
    shift
    run=$(mktemp -d "$ROOT/logs/verifier-XXXXXXXX")
    mkdir -p "$run/tests" "$run/logs"
    cp -r "$ROOT/source/tasks/$TASK/tests/." "$run/tests/"
    binds+=(--bind "$run/tests:/tests" --bind "$run/logs:/logs")
    echo "Verifier outputs: $run/logs/verifier"
    set -- bash /tests/test.sh "$@"
fi
exec singularity exec --nv --no-home --cleanenv --contain --writable-tmpfs \
    "${binds[@]}" --pwd /workspace "${RSI_IMAGE:-/storage/openpsi/images/areal-dev-sglang-0821.sif}" \
    bash "$ROOT/scripts/inside.sh" "$@"
