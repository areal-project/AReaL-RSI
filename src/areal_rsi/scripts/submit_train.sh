#!/usr/bin/env bash
# Submit a CPU controller; AReaL schedules all GPU workers from the supplied config.
set -euo pipefail
set +x

if [[ ${1:-} != --controller ]]; then
  export RSI_ROOT=${RSI_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd -P)}
fi
for name in RSI_ROOT AREAL_ROOT TRAIN_IMAGE RUN_ROOT CONFIG_PATH; do
  [[ -n ${!name:-} && ${!name} == /* ]] || { echo "Set $name to an absolute path" >&2; exit 2; }
  export "$name"
done
: "${RECIPE:?Set RECIPE to the preparation function import path}"
: "${TRAIN_PYTHON:=python3}"
: "${CONTAINER_BIN:=singularity}"
: "${CONTROLLER_MOUNTS:?Set shared storage and Slurm/munge container mounts}"
: "${WORKER_MOUNTS:?Set worker container mounts for source, model and output}"
[[ -s "$CONFIG_PATH" && -r "$TRAIN_IMAGE" ]] || { echo "Config and image must exist" >&2; exit 2; }
export RECIPE TRAIN_PYTHON CONTAINER_BIN CONTROLLER_MOUNTS WORKER_MOUNTS
export PYTHONPATH="$AREAL_ROOT:$RSI_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export SLURM_EXPORT_ENV=ALL
mkdir -p "$RUN_ROOT"
if [[ ${1:-} != --controller ]]; then
  # Store exact argv for the spooled controller, without shell interpolation.
  python3 - "$RUN_ROOT/recipe-args.json" "$@" <<'PYARGS'
import json
import os
import sys
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as stream:
    json.dump(sys.argv[2:], stream)
PYARGS
  exec sbatch --parsable --nodes=1 --ntasks=1 --cpus-per-task=4 --mem=16G \
    --gres=gpu:0 --time="${CONTROLLER_TIME:-04:00:00}" --no-requeue --export=ALL \
    --job-name="${TRIAL_NAME:-rsi}-controller" --chdir="$RSI_ROOT" \
    --output="$RUN_ROOT/controller-%j.log" \
    "$RSI_ROOT/src/areal_rsi/scripts/submit_train.sh" --controller
fi
export SINGULARITY_BIND="$WORKER_MOUNTS" APPTAINER_BIND="$WORKER_MOUNTS"
export PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
exec "$CONTAINER_BIN" exec --no-eval --pid --writable-tmpfs \
  --bind "$CONTROLLER_MOUNTS" "$TRAIN_IMAGE" "$TRAIN_PYTHON" - <<'PYTRAIN'
import json
import os
from pathlib import Path
from areal_rsi.scripts.train import main
args = json.loads((Path(os.environ["RUN_ROOT"]) / "recipe-args.json").read_bytes())
main(["--recipe", os.environ["RECIPE"], "--config", os.environ["CONFIG_PATH"], *args])
PYTRAIN
