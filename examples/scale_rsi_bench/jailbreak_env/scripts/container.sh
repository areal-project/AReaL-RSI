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
for name in OPENAI_API_KEY OPENAI_BASE_URL JAILBREAK_JUDGE_MODEL JAILBREAK_JUDGE_CONCURRENCY; do
    if [[ -n ${!name:-} ]]; then export "SINGULARITYENV_${name}=${!name}"; fi
done
candidate_workspace=${RSI_CANDIDATE_WORKSPACE:-$ROOT/workspace}
candidate_workspace=$(cd -- "$candidate_workspace" && pwd)
if [[ ${RSI_RESTRICTED:-0} == 1 ]]; then
    export SINGULARITYENV_RSI_ROOT=/rsi
    export SINGULARITYENV_UV_CACHE_DIR=/tmp/uv-cache
    export SINGULARITYENV_TORCHINDUCTOR_CACHE_DIR=/tmp/torch-cache
    export SINGULARITYENV_TRITON_CACHE_DIR=/tmp/triton-cache
    binds=(--bind "$candidate_workspace:/workspace"
           --bind "$(readlink -f -- "$ROOT/scripts/inside.sh"):/rsi/scripts/inside.sh:ro"
           --bind "$(readlink -f -- "$ROOT/config.sh"):/rsi/config.sh:ro"
           --bind "$ROOT/.venv:/rsi/.venv:ro"
           --bind "$ROOT/assets:/opt/eval_assets:ro" --bind "$ROOT/eval_src:/opt/eval_src:ro"
           --bind "$ROOT/nltk_data:/opt/nltk_data:ro"
           --bind "$ROOT/cache/huggingface:/opt/hf_cache:ro")
    inside=/rsi/scripts/inside.sh
    for model in "$ROOT"/models/*; do
        model_snapshot=$(readlink -f -- "$model")
        model_store=$(dirname -- "$(dirname -- "$model_snapshot")")
        binds+=(--bind "$model_snapshot:/opt/models/$(basename -- "$model"):ro"
                --bind "$model_store/blobs:/opt/blobs:ro")
    done
    mount_options=(--no-mount /storage)
else
    binds=(--bind /storage:/storage --bind "$candidate_workspace:/workspace"
           --bind "$ROOT/models:/opt/models:ro" --bind "$ROOT/assets:/opt/eval_assets"
           --bind "$ROOT/eval_src:/opt/eval_src" --bind "$ROOT/nltk_data:/opt/nltk_data"
           --bind "$ROOT/cache/huggingface:/opt/hf_cache")
    inside="$ROOT/scripts/inside.sh"
    mount_options=()
fi
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
    "${mount_options[@]}" "${binds[@]}" --pwd /workspace "${RSI_IMAGE:-/storage/openpsi/images/areal-dev-sglang-0821.sif}" \
    bash "$inside" "$@"
