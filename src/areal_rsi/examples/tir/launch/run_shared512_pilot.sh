#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../../../.." && pwd)
MAIN=/storage/openpsi/users/fenghui/projects/AReaL-RSI-dev-0928
AREAL=/storage/openpsi/users/fenghui/projects/AReaL-dev-0928
IMAGE=/storage/openpsi/images/areal-dev-sglang-0821.sif
PYTHON=/storage/openpsi/users/fenghui/rsi-tir-0929/venv/bin/python
CLAUDE=/storage/openpsi/users/fenghui/claude
PARENT=/storage/openpsi/users/fenghui/projects/AReaL-RSI-trainonly-20261003/data/trainonly-v2
MODEL=$PARENT/areal-experiments/checkpoints/root/rsi-shared512-pilot/g2-shared512-2ep-20261003/default/epoch1epochstep15globalstep31
DATA=$ROOT/data/verifier-continuation-v1
TASKS=$PARENT/taskset-v2
PROMPT=$PARENT/g2-eval/m1-selected-dev/harness/prompt.txt

export PYTHONPATH="$AREAL:$ROOT/src"
export NO_PROXY='*' no_proxy='*' HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
cd "$ROOT"
"$PYTHON" - "$TASKS" "$DATA" "$PARENT/g2-eval/decision.json" <<'PY'
import hashlib
import sys
from pathlib import Path
from areal_rsi.api.control import json_bytes
from areal_rsi.infra.storage.files import write_bytes
from areal_rsi.infra.coordination.evaluation.tasks import load_task_set
task_set = load_task_set(Path(sys.argv[1]))
assert len(task_set.model_train.task_ids) == 512
assert len(task_set.model_dev.task_ids) == 256
assert task_set.harness_evolve == task_set.model_train
assert task_set.harness_select == task_set.model_train
write_bytes(Path(sys.argv[2]) / 'plan-loop-v1.json', json_bytes({
    'first_generation': 3,
    'parent_decision_path': sys.argv[3],
    'parent_decision_digest': 'sha256:' + hashlib.sha256(Path(sys.argv[3]).read_bytes()).hexdigest(),
    'continuation_reason': 'Re-evaluate generation 3 after exact factorial comparison repair.',
    'task_set_id': task_set.task_set_id,
    'training_cases': 512,
    'test_cases': 256,
    'maximum_generations': 6,
    'stop_on_paired_test_drop': 0.02,
    'candidate_count': 4,
    'on_no_improvement': 'reuse',
    'epochs_per_generation': 2,
    'synthetic_stage': 'After a terminal weight-loop decision; 512 pure synthetic training cases.',
    'monitor_194_used': False,
}), immutable=True)
PY

for GENERATION in 3 4 5 6; do
SEARCH=$DATA/g${GENERATION}-search
EVAL=$DATA/g${GENERATION}-eval
CONFIG=$DATA/train-g${GENERATION}-2ep.yaml
LOCK=$DATA/g${GENERATION}-source-lock.json
TRIAL=g${GENERATION}-shared512-2ep-20261003
CHECKPOINT=$DATA/areal-experiments/checkpoints/root/rsi-shared512-pilot/$TRIAL/default/epoch1epochstep15globalstep31

if [[ ! -e "$EVAL/decision.json" ]]; then
bash "$ROOT/src/areal_rsi/examples/tir/launch/run_shared512_search.sh" \
  "$MODEL" "$TASKS" "$PROMPT" "$SEARCH"
read -r SELECTED < "$SEARCH/selected-harness-path.txt"

"$PYTHON" - "$MAIN" "$ROOT" "$MODEL" "$TASKS" "$SEARCH" "$SELECTED" "$CONFIG" "$LOCK" "$TRIAL" <<'PY'
import hashlib
import json
import subprocess
import sys
from pathlib import Path
import yaml
from areal_rsi.api.control import json_bytes
from areal_rsi.infra.storage.files import write_bytes
from areal_rsi.examples.tir.claude_solver import load_claude_harness
from areal_rsi.infra.coordination.evaluation.tasks import load_task_set

main, root, model, tasks, search, selected, config_path, lock_path = map(Path, sys.argv[1:9])
trial = sys.argv[9]
template = main / 'data/claude-unified-1p5b-2epoch/train-g1-current-h0-2ep.yaml'
config = yaml.safe_load(template.read_text())
config['experiment_name'] = 'rsi-shared512-pilot'
config['trial_name'] = trial
config['cluster']['fileroot'] = str(config_path.parent / 'areal-experiments')
config['cluster']['name_resolve']['nfs_record_root'] = str(config_path.parent / 'name-resolve')
config['actor']['path'] = str(model)
config['ref']['path'] = str(model)
config['train_dataset']['path'] = str(tasks / 'public/model_train.jsonl')
config['valid_dataset']['path'] = str(tasks / 'public/model_dev.jsonl')
config['actor']['scheduling_spec'][0]['env_vars']['PYTHONPATH'] = ':'.join(
    (str(Path('/storage/openpsi/users/fenghui/projects/AReaL-dev-0928')), str(root / 'src'), str(root / 'src'))
)
write_bytes(config_path, yaml.safe_dump(config, sort_keys=False).encode(), immutable=True)
task_set = load_task_set(tasks)
harness, _ = load_claude_harness(selected, Path('/storage/openpsi/users/fenghui/claude'), model)
decision = json.loads((search / 'model-candidates/decision.json').read_bytes())
if decision['selected_harness_id'] != harness.harness_id:
    raise ValueError('Selected Harness differs from frozen search')
def digest(path):
    return 'sha256:' + hashlib.sha256(Path(path).read_bytes()).hexdigest()
lock = {
    'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
    'task_set_id': task_set.task_set_id,
    'model_root': str(model),
    'harness_id': harness.harness_id,
    'selected_harness_root': str(selected),
    'training_config_digest': digest(config_path),
    'search_decision_digest': digest(search / 'model-candidates/decision.json'),
    'experiment_plan_digest': digest(tasks.parent / 'plan-loop-v1.json'),
    'decision_source_digests': {name: digest(main / 'data/claude-unified-1p5b' / name) for name in (
        'decide-next-generation.py', 'g1-c10-plan.json', 'plan-native4k-v2.json',
    )},
    'source_digests': {name: digest(root / name) for name in (
        'src/areal_rsi/api/records.py',
        'src/areal_rsi/infra/coordination/evaluation/tasks.py',
        'src/areal_rsi/examples/tir/claude_solver.py',
        'src/areal_rsi/infra/runtime/anthropic.py',
        'src/areal_rsi/infra/runtime/claude.py',
        'src/areal_rsi/examples/tir/launch/run_shared512_search.sh',
        'src/areal_rsi/examples/tir/launch/run_shared512_eval.sh',
        'src/areal_rsi/examples/tir/claude_train.py',
        'src/areal_rsi/examples/tir/launch/run_shared512_pilot.sh',
    )},
}
write_bytes(lock_path, json_bytes(lock), immutable=True)
PY

singularity exec --no-eval --nv --bind /storage:/storage \
  --env "PYTHONPATH=$PYTHONPATH,HF_HUB_OFFLINE=1,TRANSFORMERS_OFFLINE=1,NO_PROXY=*,no_proxy=*" \
  "$IMAGE" python3 -m areal_rsi.scripts.train --recipe areal_rsi.examples.tir.claude_train.prepare actor._version=v2 rollout._version=v2 \
  --task-set-root "$TASKS" --harness-root "$SELECTED" \
  --claude-cli "$CLAUDE" --validate-only --config "$CONFIG"
if ! grep -q 'Training completes!' "$DATA/g${GENERATION}-train.log"; then
singularity exec --no-eval --nv --bind /storage:/storage \
  --env "PYTHONPATH=$PYTHONPATH,HF_HUB_OFFLINE=1,TRANSFORMERS_OFFLINE=1,NO_PROXY=*,no_proxy=*" \
  "$IMAGE" python3 -m areal_rsi.scripts.train --recipe areal_rsi.examples.tir.claude_train.prepare actor._version=v2 rollout._version=v2 \
  --task-set-root "$TASKS" --harness-root "$SELECTED" \
  --claude-cli "$CLAUDE" --config "$CONFIG" \
  > "$DATA/g${GENERATION}-train.log" 2>&1
fi
grep -q 'Training completes!' "$DATA/g${GENERATION}-train.log"
"$PYTHON" -c 'import sys; from pathlib import Path; from areal_rsi.infra.storage.artifacts import validate_hf_model_files; validate_hf_model_files(Path(sys.argv[1]))' "$CHECKPOINT"

for ((i=0; i<180; i++)); do
  if [[ -z "$(nvidia-smi --query-compute-apps=pid --format=csv,noheader)" ]]; then break; fi
  sleep 2
done
test -z "$(nvidia-smi --query-compute-apps=pid --format=csv,noheader)"

bash "$ROOT/src/areal_rsi/examples/tir/launch/run_shared512_eval.sh" \
  "$CHECKPOINT" "$TASKS" model_dev "shared512-g${GENERATION}-m1-selected-test" \
  "$EVAL/m1-selected-dev" "$SELECTED/prompt.txt"
if [[ "$SELECTED" != "$SEARCH/incumbent-harness" ]]; then
  bash "$ROOT/src/areal_rsi/examples/tir/launch/run_shared512_eval.sh" \
    "$CHECKPOINT" "$TASKS" model_dev "shared512-g${GENERATION}-m1-h0-test" \
    "$EVAL/m1-h0-dev" "$SEARCH/incumbent-harness/prompt.txt"
fi
"$PYTHON" "$MAIN/data/claude-unified-1p5b/decide-next-generation.py" \
  --generation "$GENERATION" --task-set-root "$TASKS" --search-root "$SEARCH" \
  --post-root "$EVAL" --output "$EVAL/decision.json"
fi

readarray -t NEXT < <("$PYTHON" - "$EVAL/decision.json" "$GENERATION" <<'PY'
import json
import sys
from pathlib import Path
decision = json.loads(Path(sys.argv[1]).read_bytes())
generation = int(sys.argv[2])
action = decision['decision']
if decision['generation'] != generation or action not in (
    'stop_weight_loop',
    'complete_six_generations' if generation == 6 else f'continue_to_generation_{generation + 1}',
):
    raise ValueError('Unexpected generation decision')
print(action)
print(decision['retained_model_root'])
print(Path(decision['retained_harness_root']) / 'prompt.txt')
PY
)
test "${#NEXT[@]}" -eq 3
MODEL=${NEXT[1]}
PROMPT=${NEXT[2]}
if [[ "${NEXT[0]}" == stop_weight_loop || "${NEXT[0]}" == complete_six_generations ]]; then
  break
fi
done

"$PYTHON" - "$EVAL/decision.json" "$EVAL/retained-state.json" "$TASKS" <<'PY'
import json
import sys
from pathlib import Path
from areal_rsi.api.control import json_bytes
from areal_rsi.infra.storage.files import write_bytes
decision_path, output, tasks = map(Path, sys.argv[1:])
decision = json.loads(decision_path.read_bytes())
generation = decision['generation']
if decision['decision'] not in ('stop_weight_loop', 'complete_six_generations'):
    raise ValueError('Cannot start synthesis before the weight loop terminates')
write_bytes(output, json_bytes({
    'terminal_generation': generation,
    'parent_generation': generation - 1 if decision['decision'] == 'stop_weight_loop' else generation,
    'decision_path': str(decision_path),
    'model_root': decision['retained_model_root'],
    'harness_root': decision['retained_harness_root'],
    'task_set_root': str(tasks),
}), immutable=True)
PY
