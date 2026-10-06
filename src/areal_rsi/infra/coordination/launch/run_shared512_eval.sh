#!/usr/bin/env bash
set -euo pipefail
MODEL=$(realpath "${1:?model checkpoint or base model}")
TASKS=$(realpath "${2:?frozen task set}")
SPLIT=${3:?evaluation split}
TRIAL=${4:?trial identifier}
OUT=$(realpath -m "${5:?new output directory}")
PROMPT=$(realpath "${6:?selected frozen Harness prompt}")
RSI=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../../../.." && pwd)
AREAL=/storage/openpsi/users/fenghui/projects/AReaL-dev-0928
IMAGE=/storage/openpsi/images/areal-dev-sglang-0821.sif
PYTHON=/storage/openpsi/users/fenghui/rsi-tir-0929/venv/bin/python
export PYTHONPATH="$RSI/src"
export NO_PROXY='*' no_proxy='*' HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
if curl -fsSI http://127.0.0.1:31950/ >/dev/null 2>&1; then
  echo 'Port 31950 already serves another model' >&2
  exit 1
fi
mkdir -p "$OUT"
if [[ ! -e "$OUT/harness" ]]; then
  "$PYTHON" -m areal_rsi.infra.runtime.claude_solver freeze \
    --prompt "$PROMPT" \
    --claude-cli /storage/openpsi/users/fenghui/claude \
    --backend-model-root "$MODEL" --model "$MODEL" --output "$OUT/harness"
fi
"$PYTHON" - "$OUT/harness/manifest.json" "$PROMPT" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text())
prompt_digest = "sha256:" + hashlib.sha256(Path(sys.argv[2]).read_bytes()).hexdigest()
if manifest["prompt_digest"] != prompt_digest:
    raise ValueError("Checkpoint evaluation Harness prompt differs from the selected prompt")
PY
template_args=()
if [[ -n "${RSI_EVAL_CHAT_TEMPLATE:-}" ]]; then
  template_args=(--chat-template "$RSI_EVAL_CHAT_TEMPLATE")
fi
setsid env CUDA_VISIBLE_DEVICES=0 singularity exec --no-eval --nv --bind /storage:/storage \
  --env 'HF_HUB_OFFLINE=1,TRANSFORMERS_OFFLINE=1,NO_PROXY=*,no_proxy=*,CUDA_VISIBLE_DEVICES=0' \
  "$IMAGE" python3 -m sglang.launch_server \
  --model-path "$MODEL" --host 127.0.0.1 --port 31950 \
  --mem-fraction-static "${RSI_EVAL_MEMORY_FRACTION:-0.35}" --context-length "${RSI_EVAL_CONTEXT_LENGTH:-4096}" \
  --tool-call-parser qwen --disable-piecewise-cuda-graph ${template_args[@]+"${template_args[@]}"} \
  >> "$OUT/server.log" 2>&1 &
SERVER_PID=$!
trap 'kill -- -"$SERVER_PID" 2>/dev/null || true; wait "$SERVER_PID" 2>/dev/null || true' EXIT
for ((i=0; i<180; i++)); do
  if curl -fsSI http://127.0.0.1:31950/ >/dev/null 2>&1; then
    break
  fi
  kill -0 "$SERVER_PID"
  sleep 2
done
curl -fsSI http://127.0.0.1:31950/ >/dev/null
kill -0 "$SERVER_PID"
"$PYTHON" -m areal_rsi.infra.runtime.claude_solver evaluate \
  --task-set-root "$TASKS" --harness-root "$OUT/harness" \
  --claude-cli /storage/openpsi/users/fenghui/claude \
  --backend-model-root "$MODEL" --backend-url http://127.0.0.1:31950 \
  --runner-root "$AREAL/examples/tir" --split "$SPLIT" \
  --trial-id "$TRIAL" --output "$OUT/report.json" --workers 4
