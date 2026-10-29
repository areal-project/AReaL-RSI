#!/usr/bin/env bash
set -euo pipefail
set +x
umask 077
run_dir=$1
mode=${2:-}
source "$run_dir/environment.sh"
: "${CREDENTIALS_FILE:?Set --credentials-file or CREDENTIALS_FILE}"
set -a
source "$CREDENTIALS_FILE"
set +a
# Reapply deployment settings if an older credential file also exports them.
source "$run_dir/environment.sh"
: "${ARENA_OPENAPI_BASE:?Credentials must export ARENA_OPENAPI_BASE}"
: "${ARENA_OPENAPI_TOKEN:?Credentials must export ARENA_OPENAPI_TOKEN}"
export ARENA_LLM_API_KEY=${ARENA_LLM_API_KEY:-$ARENA_OPENAPI_TOKEN}
export SWE_RL_ADMIN_API_KEY=${SWE_RL_ADMIN_API_KEY:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')}
if [[ -n ${WANDB_API_KEY:-} && -n ${WANDB_BASE_URL:-} ]]; then
  export WANDB_MODE=online
else
  export WANDB_MODE=disabled
fi
echo "W&B automatic mode: $WANDB_MODE"
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy

# Both commands run on the allocated CPU node, with the actual role images.
# In particular, do not infer the rollout runtime from the actor SIF.
singularity exec --no-eval --pid --writable-tmpfs \
  --bind "$AREAL_ROLLOUT_BINDS" --env "PYTHONPATH=$AREAL_ROLLOUT_PYTHONPATH" \
  "$AREAL_ROLLOUT_IMAGE" "$AREAL_ROLLOUT_PYTHON" \
  "$run_dir/controller.py" --rollout-runtime

singularity exec --no-eval --pid --writable-tmpfs \
  --bind "$AREAL_CONTROLLER_BINDS" --env "PYTHONPATH=$AREAL_ACTOR_PYTHONPATH" \
  "$AREAL_ACTOR_IMAGE" "$AREAL_ACTOR_PYTHON" \
  "$run_dir/controller.py" ${mode:+"$mode"} &
controller_pid=$!
forward_signal() {
  kill -TERM "$controller_pid" 2>/dev/null || true
  wait "$controller_pid" || true
  exit 143
}
trap forward_signal TERM INT
wait "$controller_pid"
