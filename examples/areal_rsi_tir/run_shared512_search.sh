#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
AREAL=/storage/openpsi/users/fenghui/projects/AReaL-dev-0928
IMAGE=/storage/openpsi/images/areal-dev-sglang-0821.sif
PYTHON=/storage/openpsi/users/fenghui/rsi-tir-0929/venv/bin/python
CLAUDE=/storage/openpsi/users/fenghui/claude
MODEL=$(realpath "${1:?base model or checkpoint}")
TASKS=$(realpath "${2:?frozen shared task set}")
PROMPT=$(realpath "${3:?incumbent prompt}")
OUT=$(realpath -m "${4:?search output}")
PORT=31950

export PYTHONPATH="$ROOT/src:$ROOT/examples"
export NO_PROXY='*' no_proxy='*' HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
if curl -fsSI "http://127.0.0.1:$PORT/" >/dev/null 2>&1; then
  echo 'Port 31950 already serves another model' >&2
  exit 1
fi
mkdir -p "$OUT"
if [[ ! -e "$OUT/incumbent-harness" ]]; then
  "$PYTHON" -m areal_rsi_tir.claude_code freeze \
    --prompt "$PROMPT" --claude-cli "$CLAUDE" \
    --backend-model-root "$MODEL" --model "$MODEL" \
    --output "$OUT/incumbent-harness"
fi
setsid env CUDA_VISIBLE_DEVICES=0 singularity exec --no-eval --nv --bind /storage:/storage \
  --env 'HF_HUB_OFFLINE=1,TRANSFORMERS_OFFLINE=1,NO_PROXY=*,no_proxy=*,CUDA_VISIBLE_DEVICES=0' \
  "$IMAGE" python3 -m sglang.launch_server \
  --model-path "$MODEL" --host 127.0.0.1 --port "$PORT" \
  --mem-fraction-static 0.35 --context-length 4096 \
  --tool-call-parser qwen --disable-piecewise-cuda-graph \
  >> "$OUT/server.log" 2>&1 &
SERVER_PID=$!
trap 'kill -- -"$SERVER_PID" 2>/dev/null || true; wait "$SERVER_PID" 2>/dev/null || true' EXIT
for ((i=0; i<180; i++)); do
  if curl -fsSI "http://127.0.0.1:$PORT/" >/dev/null 2>&1; then break; fi
  kill -0 "$SERVER_PID"
  sleep 2
done
curl -fsSI "http://127.0.0.1:$PORT/" >/dev/null
common=(--task-set-root "$TASKS" --claude-cli "$CLAUDE" \
  --backend-model-root "$MODEL" --backend-url "http://127.0.0.1:$PORT" \
  --runner-root "$AREAL/examples/tir")
for pair in 'h0-evolve:harness_evolve' 'h0-select:harness_select' 'h0-repeat:harness_select'; do
  name=${pair%%:*}
  split=${pair#*:}
  "$PYTHON" -m areal_rsi_tir.claude_code evaluate \
    "${common[@]}" --harness-root "$OUT/incumbent-harness" \
    --split "$split" --trial-id "shared512-$name" \
    --output "$OUT/$name.json" --workers 4
done
"$PYTHON" -m areal_rsi_tir.claude_code search \
  "${common[@]}" --harness-root "$OUT/incumbent-harness" \
  --baseline-evolve "$OUT/h0-evolve.json" \
  --baseline-select "$OUT/h0-select.json" \
  --baseline-repeat "$OUT/h0-repeat.json" \
  --output-root "$OUT/candidates" --candidate-count 4 \
  --workers-per-candidate 1 --on-no-improvement reuse
SELECTED=$("$PYTHON" - "$OUT" <<'PY'
import json
import sys
from pathlib import Path
root = Path(sys.argv[1])
decision = json.loads((root / 'candidates/decision.json').read_bytes())
selected_id = decision['selected_harness_id']
choices = [root / 'incumbent-harness'] + sorted((root / 'candidates').glob('candidate-*/harness'))
selected = [path for path in choices if (path / 'manifest.json').is_file()
            and json.loads((path / 'manifest.json').read_bytes())['harness_id'] == selected_id]
if len(selected) != 1 or not decision['continue_to_training']:
    raise ValueError('Search did not select exactly one training Harness')
print(selected[0])
PY
)
"$PYTHON" -m areal_rsi_tir.claude_code evaluate \
  "${common[@]}" --harness-root "$OUT/incumbent-harness" \
  --split model_dev --trial-id shared512-m0-h0-dev \
  --output "$OUT/m0-h0-dev.json" --workers 4
if [[ "$SELECTED" != "$OUT/incumbent-harness" ]]; then
  "$PYTHON" -m areal_rsi_tir.claude_code evaluate \
    "${common[@]}" --harness-root "$SELECTED" \
    --split model_dev --trial-id shared512-m0-selected-dev \
    --output "$OUT/m0-selected-dev.json" --workers 4
fi
printf '%s\n' "$SELECTED" > "$OUT/selected-harness-path.txt"
