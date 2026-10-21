#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")/env_root.sh"
source "$ROOT/config.sh"
mkdir -p "$ROOT"/{workspace,models,assets,eval_src,nltk_data,cache/huggingface,cache/uv,cache/torch,cache/triton,logs}
export APPTAINERENV_RSI_ROOT="$ROOT"
export APPTAINERENV_HF_HOME=/opt/hf_cache
export APPTAINERENV_UV_CACHE_DIR="$ROOT/cache/uv"
export APPTAINERENV_TORCHINDUCTOR_CACHE_DIR="$ROOT/cache/torch"
export APPTAINERENV_TRITON_CACHE_DIR="$ROOT/cache/triton"
export APPTAINERENV_NLTK_DATA=/opt/nltk_data
export APPTAINERENV_PYTHONNOUSERSITE=1
export APPTAINERENV_OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-8}"
# Preserve Slurm's physical GPU allocation inside Apptainer. Using the legacy
# SINGULARITYENV prefix can make two four-GPU jobs on one node address the same
# physical devices even though Slurm assigned disjoint GRES indices.
export APPTAINERENV_CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-}"
candidate_workspace=${RSI_CANDIDATE_WORKSPACE:-$ROOT/workspace}
candidate_workspace=$(cd -- "$candidate_workspace" && pwd)
if [[ ${RSI_RESTRICTED:-0} == 1 ]]; then
    export APPTAINERENV_RSI_ROOT=/rsi
    export APPTAINERENV_UV_CACHE_DIR=/tmp/uv-cache
    export APPTAINERENV_TORCHINDUCTOR_CACHE_DIR=/tmp/torch-cache
    export APPTAINERENV_TRITON_CACHE_DIR=/tmp/triton-cache
    binds=(--bind "$candidate_workspace:/workspace"
           --bind "$(readlink -f -- "$ROOT/scripts/inside.sh"):/rsi/scripts/inside.sh:ro"
           --bind "$(readlink -f -- "$ROOT/config.sh"):/rsi/config.sh:ro"
           --bind "$(readlink -f -- "$ROOT/source/tasks/$TASK/tests/train_capped.sh"):/rsi/benchmark/train_capped.sh:ro"
           --bind "$(readlink -f -- "$ROOT/source/tasks/$TASK/tests/eval_official.py"):/rsi/benchmark/eval_official.py:ro"
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
    echo 'Unsafe upstream OPSD verifier disabled: GPU cleanup is not allocation-scoped. Use dev_eval.sh.' >&2
    exit 2
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
