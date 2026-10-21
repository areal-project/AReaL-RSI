#!/usr/bin/env bash
# Run inside a CPU Slurm allocation, from any directory.
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy
export APPTAINER_CACHEDIR="$root/cache/apptainer"
export APPTAINER_TMPDIR="${SLURM_TMPDIR:-/tmp}/verilog-build-${SLURM_JOB_ID:-$$}"
mkdir -p "$APPTAINER_TMPDIR" images logs
trap 'rm -rf -- "$APPTAINER_TMPDIR"' EXIT
bench=${1:?Usage: build.sh rtllm|verilogeval|cvdp}
case "$bench" in
    rtllm) name=rtllm-v2.1-icarus; ref=v12_0; major=12; py=3.11; full=0 ;;
    verilogeval) name=verilogeval-v2; ref=v12_0; major=12; py=3.11; full=0 ;;
    cvdp) name=cvdp-v1.1.0-oss; ref=v13_0; major=13; py=3.12; full=1 ;;
    *) exit 2 ;;
esac
if [[ "$bench" = cvdp ]]; then
    base=${VERILOG_BASE_IMAGE:-"$root/images/ubuntu-24.04.sif"}
    if [[ ! -f "$base" ]]; then
        base_root="$APPTAINER_TMPDIR/ubuntu-root"
        mkdir -p "$base_root"
        tar -xzf cache/direct/ubuntu24base.tar.gz -C "$base_root"
        apptainer build "$base" "$base_root"
    fi
else
    base=${VERILOG_BASE_IMAGE:-"$root/images/debian-12.sif"}
    if [[ ! -f "$base" ]]; then
        apptainer pull "$base" docker://debian:12
    fi
fi
image="$root/images/$name.sif"
[[ ! -e "$image" ]] || { printf 'Image already exists: %s\n' "$image" >&2; exit 1; }
apptainer build --build-arg BASE_IMAGE="$base" --build-arg BENCH="$bench" \
    --build-arg ICARUS_REF="$ref" --build-arg ICARUS_MAJOR="$major" \
    --build-arg PYTHON_VERSION="$py" \
    --build-arg PYTHON_PROFILE="$([[ "$bench" = cvdp ]] && printf cvdp || printf rtl)" \
    --build-arg PYPI_INDEX_URL="${VERILOG_PYPI_INDEX_URL:-https://pypi.org/simple}" \
    --build-arg FULL_EDA="$full" --build-arg JOBS="${SLURM_CPUS_PER_TASK:-8}" \
    "$image" containers/benchmark.def
sha256sum "$image" > "$image.sha256"
apptainer inspect --json "$image" > "$image.inspect.json"
apptainer exec --cleanenv "$image" cat /opt/installed-requirements.txt > "$image.requirements.txt"
