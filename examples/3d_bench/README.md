# 3D Benchmark reproduction

## Full RSI workflow: one diagnostic round

```bash
uv run --env-file .env --locked python examples/3d_bench/run_full.py \
  --tasks /Users/jjsong/Downloads/model_tasks_100
```

This starts real paid calls using the existing `.env`: RSI Agent plans one round;
the standard `LocalExperimentService` / `HarnessEvolutionWorkflow` evaluates
the baseline, generates TRAIN evidence, asks Research Agent for one prompt-only
change, evaluates the candidate, and asks Research Agent to select a version.
There are three Task Agent generations, one research proposal and one selection.
RSI Agent does planning, not direct tool dispatch to the other agents. One research
line needs no RSI pruning call. No TUI confirmation is used: invoking this script
authorizes exactly one round, enforced independently of the model's plan.

MODEL-001 is deliberately reused in TRAIN and VALIDATE for this wiring diagnostic.
There is no held-out score, visual judge, or claim of generalization. Only the
Contract V2 validator is used. All generations use the same target connection,
public task snapshot, template, explicit ESM package, tools and request budget.
Each call gets a fresh model file. Research edits instructions, not model.js.

Everything is saved under `outputs/model001_full_*/`: `rsi-request.txt`,
`rsi-plan.json`, `events.log`, `result.json`, baseline/candidate Harnesses,
`tasks/*/workspace/src/model.js`, isolated `verification/`, actual `trajectories/`,
and the standard `experiment/runs/*/` checkpoints. The canonical model traces
are not rewritten. The standard workflow now saves an EvidenceBundle containing
separate task, output, trajectory, full verification and report files. Research
receives a selected file view under `research-workspaces/research_*/train/`:
`index.json`, `report.json`, and `tasks/1/{task.json,output,verification.json,trajectory.json}`.
Here `output` is exactly the generated model.js bytes. Validator receipts are
separate `tasks/1/attachments/*` files. The proposal's initial user message contains
paths and reading rules, not inline diagnostics or source code.

The example selects accessible file kinds and prescribes reading verification
first, then small relevant output/trajectory excerpts. Evidence originals are
never truncated; bash responses still have a display limit, so oversized reads
must continue in smaller portions. Actual tool calls/results remain in the
Research trajectory. A sibling `research_*.evidence.json` records the source and
exact granted manifest. Runtime sandboxing prevents access to backing content,
other workspaces and unauthorized files, including via symlinks.

This replaces the temporary direct-feedback and `host_evidence` bridge. Neither
entrypoint injects bounded verifier feedback into the first model message anymore.
Both use the generic EvidenceStore API; no 3D logic is added to `src`.
The full driver is assembled through public workflow interfaces, not the TUI.
New full runs record `research_feedback_delivery: evidence-files-v1` in `inputs.json`;
old direct-feedback runs are preserved and cannot be resumed under the new mode.
File delivery does not guarantee that the model reads correctly or improves scores.
The final run's `succeeded` means the workflow completed, not that the model
passed. The program computes `best_solution_id` from observed validation scores,
preferring the baseline on ties; the research selection is recorded separately.
Completed workflow checkpoints can be resumed without replanning or re-proposing:

```bash
uv run --env-file .env --locked python examples/3d_bench/run_full.py \
  --resume .rsi/datasets/3d-model-v2/outputs/model001_full_RUN_ID \
  --retry-incomplete-task
```

The saved task, template, model connections, limits and feedback mode must match.
Without `--retry-incomplete-task`, an existing incomplete generation is not retried.
With it, the incomplete files are moved to `tasks/failed-attempts/`, the failed run
state is retained in `resume-history/`, and only the unfinished generation is
restarted from its original public inputs and Harness. Original session traces
remain intact. Completed scored results are reused, never retried to improve a
score. The iteration count stays unchanged. There is no automatic retry loop.

## One research iteration

```bash
uv run --env-file .env --locked python examples/3d_bench/run_research.py \
  --baseline .rsi/datasets/3d-model-v2/outputs/model001_baseline_RUN_ID
```

This explicitly starts paid calls: one DefaultResearchAgent proposal followed
by one Task Agent regeneration. Research uses the configured `research-agent`
override or teacher connection. Target connection must match the baseline.
Only `prompt.md` can change; tools, generation limits, task and validator stay
fixed. The task is read from the saved baseline input, not Downloads.
The saved `generation_environment` must match the current runner. Old runs
without this field predate the ESM workspace fix and require a new baseline;
historical evidence is never rewritten to claim environment equivalence.
The previous failed model is evidence, not a file the researcher edits.

Outputs are under `outputs/model001_research_*/`: research trajectory, proposed
change, baseline/candidate Harness snapshots, and `comparison.json` linking both
Task Agent runs. This is a same-training-task diagnostic experiment, not a
held-out score, multi-task improvement claim or the full RSI planning workflow.
No RSI Agent planning conversation or automatic version-selection loop runs.
The one-shot driver currently composes existing research and runtime APIs in
the example; it does not yet use the generic experiment scheduler.

## Real MODEL-001 baseline

From the repository root, explicitly start one paid Task Agent run using the
target connection in your local `.env`:

```bash
uv run --env-file .env --locked python examples/3d_bench/run_baseline.py \
  --tasks /Users/jjsong/Downloads/model_tasks_100
```

This uses RSI's `DshAgentRuntime`, `task-agent`, bash tool and credential proxy,
not GameAgent or a research workflow. `baseline.md` supplies minimal instructions;
the public inputs are TARGET, the template helper prefix, Contract V2, and the
template's unchanged `package.json` declaring `"type": "module"`.
Contract V2 explicitly takes precedence over incompatible TARGET export rules.
There are no specialist skills, validator feedback retries, screenshots or VL judge.
The model endpoint must support the Chat Completions tool protocol used by RSI.

Each run creates `.rsi/datasets/3d-model-v2/outputs/model001_baseline_*/` with
input snapshots, trajectory/content records, workspace `src/model.js`, file-type
`task-result.json`, raw validator stdout/stderr and `validation.json`.
The generation budget is 4096 output tokens per request with a 600-second
request timeout. This is not a total-token cap for the full agent loop.
Validation uses a fresh runtime copy and macOS sandbox-exec with network denied.
Validation failures may include infrastructure errors: inspect the raw logs;
this diagnostic report is not a benchmark score or visual-quality assessment.

File collection currently lives in this external example. It does not change
LocalHarnessRuntime's existing text output, and does not create an Artifact
optimization workflow. Failed runs retain evidence and are not auto-retried.

### Node syntax-check caveat

On local Node v26.4.0, `node --check` can exit successfully for an invalid `.js`
file with ESM exports when the package scope has no explicit module type. The
check takes the CommonJS detection path; an ESM-only token triggers a retry
indicator, but the syntax-check caller does not recompile as ESM. Explicit
`"type": "module"` takes the actual module parser and reports the syntax error.
See Node's [syntax-check entry](https://github.com/nodejs/node/blob/v26.4.0/lib/internal/main/check_syntax.js)
and [module detection](https://github.com/nodejs/node/blob/v26.4.0/src/node_contextify.cc).

The runner copies the same package bytes into the public workspace and rejects
agent changes to that file. It does not expose validator source or install
generation dependencies. `node --check src/model.js` now uses the same ESM mode
as evaluation. For an explicit syntax-only check independent of package lookup,
use `node --input-type=module --check < src/model.js`. Neither check proves
Contract V2 compliance or visual quality. Existing failed models remain unchanged.

The `run.sh` commands below prepare and check the upstream Contract V2 runtime;
unlike the Python agent runners above, these commands do not call an LLM or
start an optimization experiment. Runtime data stays outside this example.

## Layout and provenance

- `.rsi/datasets/3d-model-v2/`: fixed viewer, helpers, validator, capture scripts,
  fixtures, lockfile, generated SHA-256 manifest, workspaces and outputs
  (Git-ignored).
- A separate workspace: writable copy; only `src/model.js` may change.
- Separate output directories: validation receipts, screenshots, run metadata.
- `benchmark.yaml` and `registry.py`: external manifest and explicit
  adapter assembly; they are not part of AReaL-RSI's built-in registry.

Source: [upstream README](https://code.alipay.com/AReaL/GameAgent/blob/xss-gdd-text2model/gdd-model-runner/README.md),
branch `xss-gdd-text2model`.
Pinned GameAgent commit `d7a9504cc6bb5b1918a084ef62225688a67c3aa6`, directory
`gdd-model-runner/viewer-template`. `init` exports that exact commit, not the
current working tree. Scripts pin and check the full snapshot digest.
Three.js is `0.185.1`; npm installs the supplied lockfile with `npm ci`.
The embedded style is `piggy-island`. No runtime files are vendored in examples.

## Reproduce

Run from the AReaL-RSI repository root with Node.js and npm installed:

```bash
bash examples/3d_bench/run.sh init /Users/jjsong/code/GameAgent
bash examples/3d_bench/run.sh prepare .rsi/datasets/3d-model-v2/workspaces/3d-stage1
bash examples/3d_bench/run.sh selftest .rsi/datasets/3d-model-v2/workspaces/3d-stage1
```

On another machine, first obtain a checkout containing the pinned commit:

```bash
git clone --branch xss-gdd-text2model --single-branch \
  https://code.alipay.com/AReaL/GameAgent.git /path/to/GameAgent
bash examples/3d_bench/run.sh init /path/to/GameAgent
```

Repository access requires your normal code.alipay.com credentials. No access
tokens are stored in these scripts or runtime metadata.

`init` and `prepare` require new destinations and never overwrite an existing
dataset or workspace. Skip these steps when reusing the setup. `prepare`
downloads npm dependencies. `selftest` runs upstream positive/negative fixtures
and the viewer build; it is not a Benchmark score. The template model is an
empty placeholder and is expected to fail candidate validation.

After a trusted implementation replaces **only the createModel body** in the
workspace's `src/model.js`, preserving the executor-owned prefix, publish that
file as the task output. The external verifier accepts a file `ContentRef` (the
file contents are the model.js artifact) and injects it into the fixed viewer.

```bash
bash examples/3d_bench/run.sh validate \
  .rsi/datasets/3d-model-v2/workspaces/3d-stage1 \
  .rsi/datasets/3d-model-v2/outputs/validation-1
CHROMIUM_BIN=/path/to/chromium \
  bash examples/3d_bench/run.sh capture \
  .rsi/datasets/3d-model-v2/workspaces/3d-stage1 \
  .rsi/datasets/3d-model-v2/outputs/capture-1
```

Output directories must be new, with existing parents; relative paths resolve
from the invoking directory. Validation produces `model-validation.json`;
capture produces model/collision composites, pixel smoke evidence, and logs.
Both record `run.json` with source/snapshot hashes, environment and execution
status. Capture additionally requires ImageMagick 7 (`magick`), DejaVu Sans
Bold, curl, Chromium and a free port (default 4173; override `MODEL_VIEW_PORT`).
The upstream capture uses 1280x800 front/right/back views and software rendering,
not the 1024x1024 three-quarter views described by Downloads/verify.

## Boundaries to resolve before Benchmark integration

1. This validator does not read TARGET.md or award a 0-100 visual score. It
   checks Contract V2 and model-declared structure. Passing does not establish
   that the task was solved. Downloads/verify is a separate scoring design.
2. The 100 TARGET.md files describe another delivery contract (standalone
   create/update/dispose exports). Contract V2 preserves an embedded helper
   prefix and uses its own actions/collision interface. A public, fixed task
   adaptation policy is needed before comparing Harnesses; do not silently
   treat the contracts as interchangeable.
3. The plain `run.sh` runner and fixture adapter are **not security sandboxes**.
   The real Python agent runners use macOS process isolation and deny verifier
   network access. The upstream validator
   imports candidate JavaScript before some source checks. Use trusted fixtures
   only at this stage; isolate generated code, credentials, network and resource
   limits before unattended evaluation. Hash checks are drift detection, not
   protection from an adversarial process or modified node_modules.
4. Keep the RSI optimization target as Harness. Generated model.js files are
   per-task outputs, not a reason to switch to ArtifactEvolutionWorkflow.

Stage 1 fixes runtime bytes and dependencies. Browser, OS, fonts and rendering
driver also affect screenshots; record/pin those in a later isolated runtime
before claiming cross-machine reproducibility.

## External adapter smoke check

The adapter can be assembled without changing the default application registry:

```bash
PYTHONPATH=examples/3d_bench \
  RSI_3D_TASKS_PATH=/Users/jjsong/Downloads/model_tasks_100 \
  RSI_3D_RUNTIME_PATH=.rsi/datasets/3d-model-v2 \
  uv run --locked python - <<'PY'
import asyncio
from pathlib import Path
from areal_rsi.base.storage import FileContentStore
from areal_rsi.api.base import BenchmarkSplit
from registry import create_3d_registry_from_environment

async def main():
    registry, config = create_3d_registry_from_environment(
        FileContentStore(Path('/tmp/rsi-3d-content')))
    bundle = registry.create('3d-model-v1', config)
    tasks = await bundle.benchmark.list_tasks(BenchmarkSplit.TRAIN)
    print(bundle.instance_id, tasks[0].task_id)

asyncio.run(main())
PY
```
