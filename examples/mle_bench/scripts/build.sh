#!/usr/bin/env bash
set -euo pipefail
source "${MLE_ROOT:-$(dirname -- "$(dirname -- "${BASH_SOURCE[0]}")")}/scripts/env.sh"
cd "$MLE_ROOT"
if [[ ${1:-} != --inside-slurm ]]; then
    python3 scripts/fetch_source.py
    mkdir -p "$MLE_ROOT/images"
    export MLE_BASE_IMAGE=${MLE_BASE_IMAGE:-/storage/openpsi/images/pytorch280-1210-v1.sif}
    [[ -f "$MLE_BASE_IMAGE" ]] || { echo "Base image missing: $MLE_BASE_IMAGE" >&2; exit 2; }
    exec sbatch --parsable --partition="${MLE_PARTITION:-all}" --nodes=1 --ntasks=1 \
        --cpus-per-task="${MLE_BUILD_CPUS:-8}" --mem="${MLE_BUILD_MEM:-48G}" \
        --time="${MLE_BUILD_TIME:-01:00:00}" --job-name=mlebench-build \
        --output="$MLE_ROOT/logs/build-%j.log" \
        "$MLE_ROOT/scripts/build.sh" --inside-slurm
fi
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy
# Build on node-local disk: metadata operations on the shared filesystem are slow.
build_tmp=$(mktemp -d /tmp/mlebench-build.XXXXXXXX)
trap 'rm -rf "$build_tmp"' EXIT
export APPTAINER_TMPDIR="$build_tmp"
export APPTAINER_CACHEDIR="$MLE_ROOT/cache/apptainer"
mkdir -p "$MLE_ROOT/cache/packages"
base_image=${MLE_BASE_IMAGE:-/storage/openpsi/images/pytorch280-1210-v1.sif}
sha256sum "$base_image" > "$MLE_ROOT/images/base-image.sha256"
apptainer inspect --json "$base_image" > "$MLE_ROOT/images/base-image.inspect.json"
cp /etc/apptainer/apptainer.conf "$build_tmp/apptainer.conf"
sed -i "s/^mksquashfs procs = .*/mksquashfs procs = ${SLURM_CPUS_PER_TASK:-8}/" "$build_tmp/apptainer.conf"
apptainer --config "$build_tmp/apptainer.conf" build --force --notest \
    --bind "$MLE_ROOT/cache/packages:/mnt" --build-arg "BASE_IMAGE=$base_image" \
    "$build_tmp/mlebench-h200.sif" container/mlebench.def
export APPTAINERENV_CUDA_VISIBLE_DEVICES=-1
export APPTAINERENV_OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-8}"
apptainer exec --cleanenv "$build_tmp/mlebench-h200.sif" python -m pip check
apptainer exec --cleanenv --bind "$MLE_ROOT/scripts:/mlebench-tools:ro" \
    "$build_tmp/mlebench-h200.sif" python /mlebench-tools/verify_environment.py
apptainer exec --cleanenv "$build_tmp/mlebench-h200.sif" cat /opt/mlebench-installed-requirements.txt \
    > "$MLE_ROOT/images/installed-requirements.txt"
cp "$build_tmp/mlebench-h200.sif" "$MLE_IMAGE.partial"
mv "$MLE_IMAGE.partial" "$MLE_IMAGE"
sha256sum "$MLE_IMAGE" > "$MLE_IMAGE.sha256"
apptainer inspect --json "$MLE_IMAGE" > "$MLE_ROOT/images/mlebench-h200.inspect.json"
echo "Built: $MLE_IMAGE"
