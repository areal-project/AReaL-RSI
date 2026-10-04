#!/usr/bin/env bash
set -euo pipefail

SYNTH=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
DATA=$SYNTH/data/synthetic-experiment
AREAL=/storage/openpsi/users/fenghui/projects/AReaL-dev-0928
IMAGE=/storage/openpsi/images/areal-dev-sglang-0821.sif
PYTHON=/storage/openpsi/users/fenghui/rsi-tir-0929/venv/bin/python
CLAUDE=/storage/openpsi/users/fenghui/claude
: "${RSI_RETAINED_STATE_PATH:?terminal state required}"
: "${RSI_TRAIN_TEMPLATE:?training template required}"

export PYTHONPATH="$AREAL:$SYNTH/src:$SYNTH/examples"
export NO_PROXY='*' no_proxy='*' HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy

for ((i=0; i<180; i++)); do
  if [[ -z "$(nvidia-smi --query-compute-apps=pid --format=csv,noheader)" ]]; then break; fi
  sleep 2
done
test -z "$(nvidia-smi --query-compute-apps=pid --format=csv,noheader)"

if [[ -z "${RSI_PROBE_BATCHES_PATH:-}" ]]; then
"$PYTHON" -m areal_rsi_tir.synthetic_experiment prepare-probe > "$DATA/prepare-probe.log"
readarray -t PROBE < <("$PYTHON" - "$DATA/probe-state.json" <<'PY'
import json
import sys
from pathlib import Path
record = json.loads(Path(sys.argv[1]).read_bytes())
for name in ("model_root", "task_set_root", "prompt_path"):
    print(record[name])
PY
)
for RUN in first second; do
  "$SYNTH/examples/areal_rsi_tir/run_synthetic_eval.sh" \
    "${PROBE[0]}" "${PROBE[1]}" harness_evolve "synthetic-probe-$RUN" \
    "$DATA/probe-$RUN" "${PROBE[2]}"
done

fi

"$PYTHON" -m areal_rsi_tir.synthetic_experiment prepare > "$DATA/prepare.log"
"$PYTHON" -m areal_rsi_tir.synthetic_experiment verify

readarray -t STATE < <("$PYTHON" - "$DATA/prepared.json" <<'PY'
import json
import sys
from pathlib import Path

record = json.loads(Path(sys.argv[1]).read_bytes())
print(record["retained"]["harness_root"])
print(record["retained"]["task_set_root"])
print(record["synthetic_task_set_root"])
print(record["control_checkpoint"])
print(record["synthetic_checkpoint"])
PY
)
PROMPT=${STATE[0]}/prompt.txt
CONTROL_TASKS=${STATE[1]}
SYNTHETIC_TASKS=${STATE[2]}
CONTROL_CHECKPOINT=${STATE[3]}
SYNTHETIC_CHECKPOINT=${STATE[4]}

for ARM in control synthetic; do
  "$PYTHON" -m areal_rsi_tir.synthetic_experiment verify
  if [[ "$ARM" == control ]]; then
    TASKS=$CONTROL_TASKS
    CHECKPOINT=$CONTROL_CHECKPOINT
  else
    TASKS=$SYNTHETIC_TASKS
    CHECKPOINT=$SYNTHETIC_CHECKPOINT
  fi
  CONFIG=$DATA/$ARM.yaml
  singularity exec --no-eval --nv --bind /storage:/storage \
    --env "PYTHONPATH=$PYTHONPATH,HF_HUB_OFFLINE=1,TRANSFORMERS_OFFLINE=1,NO_PROXY=*,no_proxy=*" \
    "$IMAGE" python3 -m areal_rsi_tir.claude_train \
    --task-set-root "$TASKS" --harness-root "${STATE[0]}" \
    --claude-cli "$CLAUDE" --validate-only --config "$CONFIG"
  singularity exec --no-eval --nv --bind /storage:/storage \
    --env "PYTHONPATH=$PYTHONPATH,HF_HUB_OFFLINE=1,TRANSFORMERS_OFFLINE=1,NO_PROXY=*,no_proxy=*" \
    "$IMAGE" python3 -m areal_rsi_tir.claude_train \
    --task-set-root "$TASKS" --harness-root "${STATE[0]}" \
    --claude-cli "$CLAUDE" --config "$CONFIG" \
    > "$DATA/$ARM-train.log" 2>&1
  grep -q 'Training completes!' "$DATA/$ARM-train.log"
  test -s "$CHECKPOINT/model.safetensors"
  for ((i=0; i<180; i++)); do
    if [[ -z "$(nvidia-smi --query-compute-apps=pid --format=csv,noheader)" ]]; then break; fi
    sleep 2
  done
  test -z "$(nvidia-smi --query-compute-apps=pid --format=csv,noheader)"
  "$SYNTH/examples/areal_rsi_tir/run_synthetic_eval.sh" \
    "$CHECKPOINT" "$TASKS" model_dev "$ARM-postloop-model-dev" \
    "$DATA/$ARM/model_dev" "$PROMPT"
done

"$PYTHON" -m areal_rsi_tir.synthetic_experiment decide-dev
"$PYTHON" -m areal_rsi_tir.synthetic_experiment verify

"$PYTHON" -m areal_rsi_tir.synthetic_experiment finalize
