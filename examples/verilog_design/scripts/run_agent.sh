#!/usr/bin/env bash
# Run on the machine where the user's HTTP proxy listens on 127.0.0.1:1081.
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
runner=${VERILOG_RUNNER_PYTHON:-python3}
: "${CODEX_HOME:?Set CODEX_HOME to the host login directory before launching}"
bench=${1:?Usage: run_agent.sh rtllm|verilogeval|cvdp --task ID [--task ID ...]}
shift
case "$bench" in rtllm|verilogeval|cvdp) ;; *) exit 2 ;; esac
export PYTHONPATH="$root/cache/runner-deps${PYTHONPATH:+:$PYTHONPATH}"
stamp=$(date +%Y%m%d-%H%M%S)-$$
endpoint="$root/cache/agent-proxy-$stamp.json"
output="$root/runs/$bench-agent-$stamp"
listen=${VERILOG_PROXY_LISTEN:-$(hostname -I | awk '{print $1}')}
mkdir -p "$root/logs" "$root/cache"
"$runner" "$root/scripts/agent_proxy.py" --listen "$listen" --output "$endpoint" \
  --upstream "${VERILOG_UPSTREAM_PROXY:-http://127.0.0.1:1081}" \
  > "$root/logs/proxy-$stamp.log" 2>&1 &
proxy_pid=$!
job_id=
cleanup() {
  if [[ -n "$job_id" ]]; then scancel "$job_id" 2>/dev/null || true; fi
  kill "$proxy_pid" 2>/dev/null || true
  wait "$proxy_pid" 2>/dev/null || true
  "$runner" -c 'import sys; from pathlib import Path; Path(sys.argv[1]).unlink(missing_ok=True)' "$endpoint"
}
trap cleanup EXIT
trap 'exit 130' INT TERM
for attempt in {1..100}; do
  [[ -s "$endpoint" ]] && break
  kill -0 "$proxy_pid" 2>/dev/null || { cat "$root/logs/proxy-$stamp.log" >&2; exit 1; }
  sleep 0.1
done
[[ -s "$endpoint" ]] || { printf 'Proxy startup timed out\n' >&2; exit 1; }
printf -v command '%q ' env "PYTHONPATH=$PYTHONPATH" "$runner" "$root/scripts/agent.py" \
  --bench "$bench" --codex-home "$CODEX_HOME" \
  --proxy-endpoint "$endpoint" --output "$output" "$@"
job_id=$(sbatch --parsable --ntasks=1 --cpus-per-task=2 --mem=8G --time=01:10:00 \
  --job-name="verilog-$bench-agent" --output="$root/logs/agent-$stamp-%j.log" --wrap="$command")
job_id=${job_id%%;*}
printf 'Job %s; output %s\n' "$job_id" "$output"
while squeue -h -j "$job_id" -o '%i' 2>/dev/null | rg -q .; do
  kill -0 "$proxy_pid" 2>/dev/null || { printf 'Proxy stopped\n' >&2; exit 1; }
  sleep 5
done
job_id=
[[ -f "$output/summary.json" ]] || { printf 'No summary; inspect job log\n' >&2; exit 1; }
"$runner" - "$output/summary.json" <<'PY'
import json
import sys
from pathlib import Path
summary = json.loads(Path(sys.argv[1]).read_text())
for result in summary['results']:
    judge = result['judge']
    print(result['task'], 'agent=', result['agent_returncode'],
          'judge=', judge['status'] if judge else None, 'error=', result['error'])
passed = summary.get('complete', False) and all(item['error'] is None and (summary['probe_only'] or
             (item['judge'] and item['judge']['status'] == 'pass')) for item in summary['results'])
raise SystemExit(0 if passed else 1)
PY
