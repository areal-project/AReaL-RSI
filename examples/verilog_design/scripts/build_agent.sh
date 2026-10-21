#!/usr/bin/env bash
# Build on a CPU node after copying the locally installed native CLI to cache/agent-build.
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"
bench=${1:?Usage: build_agent.sh rtllm|verilogeval|cvdp}
case "$bench" in
  rtllm) name=rtllm-v2.1-icarus ;;
  verilogeval) name=verilogeval-v2 ;;
  cvdp) name=cvdp-v1.1.0-oss ;;
  *) exit 2 ;;
esac
base="$root/images/$name.sif"
image="$root/images/$name-codex.sif"
[[ ! -e "$image" ]] || { printf 'Image already exists: %s\n' "$image" >&2; exit 1; }
python3 - <<'PY_CHECK'
import hashlib
import json
from pathlib import Path
lock = json.loads(Path('containers/agent-cli-lock.json').read_text())
for record in lock['files']:
    artifact = Path('cache/agent-build') / record['name']
    if hashlib.sha256(artifact.read_bytes()).hexdigest() != record['sha256']:
        raise SystemExit('Native Codex bundle does not match agent-cli-lock.json')
PY_CHECK
export APPTAINER_CACHEDIR="$root/cache/apptainer"
export APPTAINER_TMPDIR="${SLURM_TMPDIR:-/tmp}/verilog-agent-build-${SLURM_JOB_ID:-$$}"
mkdir -p "$APPTAINER_TMPDIR"
trap 'rm -rf -- "$APPTAINER_TMPDIR"' EXIT
apptainer build --build-arg BASE_IMAGE="$base" "$image" containers/agent.def
sha256sum "$image" > "$image.sha256"
apptainer inspect --json "$image" > "$image.inspect.json"
