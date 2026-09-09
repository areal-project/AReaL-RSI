# Local 3D RSI example

This directory contains one public example task, `MODEL-001`. It is a small
adapter example for the generic RSI interfaces; it is not the private 100-task
benchmark or its evaluator.

## Prerequisites

Run from the repository root on macOS, with model connections configured in
`.env`. This example requires the already-prepared local template, snapshot and
Contract V2 runtime under `.rsi/datasets/3d-model-v2/`. Those assets are not
included in this public example; a fresh checkout alone cannot run the validator.
No GameAgent process, Docker container or visual judge is started.

Check the existing assets without calling a model:

```bash
uv run --env-file .env --locked python examples/3d_bench/run_local.py --check
```

## Start From the TUI

### Developer: register once per checkout

The developer registers the trusted example application once, from the repository
root, using the same Python environment as the TUI:

```bash
uv run --locked rsi local-app register three-model examples.3d_bench.run_local:create_tui_app
```

Registration is saved in the ignored `.rsi/local-apps.json`, independently of
the experiment output directory. It imports trusted developer code; only register
implementations you trust. No model is called. Repeat registration for a new checkout;
use `--replace` to intentionally change an existing entry.

The YAML selects the registered name with `backend: three-model`. No 3D-specific
entry is built into `src`, and users do not need to register again for each run.

### User: start and confirm an experiment

Users then start the TUI from the repository root, with no benchmark-specific exports:

```bash
uv run --env-file .env --locked rsi tui
```

This is `rsi tui` with the repository's Python environment and model settings
loaded from `.env`. Neither `RSI_LOCAL_APPS` nor `RSI_DATA_ROOT` is required.
If you exported `RSI_LOCAL_APPS` while following the older instructions, run
`unset RSI_LOCAL_APPS` and remove that setting from `.env` to use the saved
project registration; an explicit environment mapping takes precedence.

In the TUI, send:

> Use examples/3d_bench/local.yaml to plan three rounds of prompt evolution for
> MODEL-001. Only modify prompt.md; keep the model, framework, tools and
> Contract V2 validator fixed. Show the plan and wait for my confirmation.

Check that the plan shows `local`, the `3d-model001-diagnostic-...` benchmark,
one research line, three iterations and the models from `.env`. Then reply:

> Confirm and start this plan.

Planning does not call the Task Agent. After confirmation, the background worker
runs baseline validation, TRAIN generation, prompt research, candidate validation
and version selection. The TUI itself handles the outer conversation; it does
not launch a second RSI conversation just to generate the plan.

Ask `Show the status of RUN_ID` to check progress, `Stop RUN_ID` to stop,
or `Resume RUN_ID; I confirm` to continue. Closing the TUI does not stop workers.
Reopen it with the same commands and workspace to find previous experiments.
Resuming an interrupted Task Agent can make a new paid generation; completed
checkpoints and the failed attempt's files are retained. Each Agent turn has a
600-second limit, including tool calls, so a model retry may be needed.

For a shorter smoke run, request one iteration when creating a **new** plan. Do not
edit an existing plan or its configuration snapshots. The registry mapping and
runtime configuration are frozen per plan; changed code or local task assets
should be tested with a new plan.

### Common startup problems

- Application not registered: run the registration command from this checkout's
  root, then restart the TUI. The registration file is local and is not in Git.
- Missing template or validator: run `--check` above. Registration does not
  download or install the private runtime assets.
- An older experiment is missing: use the same `RSI_DATA_ROOT` as when it was
  created. Changing the output directory does not move existing experiments.

## Results

Each TUI plan has an independent directory under
`.rsi/experiments/experiment_.../` by default. Optionally set `RSI_DATA_ROOT` to
choose a different output directory. The status response includes
the worker log path, which identifies that directory.

- `workers/RUN_ID.log`: chronological execution log.
- `run-states/RUN_ID.json`: status, scores, versions and iterations.
- `tasks/*/workspace/src/model.js`: generated models, with `inputs.json` in the
  task directory identifying the solution and task.
- `runs/RUN_ID/`: per-stage checkpoints and evidence references.
- `research-workspaces/`: authorized evidence files and candidate prompt.

TRAIN and VALIDATE intentionally use the same public case. A completed workflow
does not imply a passing model, visual quality or generalization improvement.

## Run Without the TUI

From the repository root:

```bash
uv run --env-file .env --locked python examples/3d_bench/run_local.py
```

The script reads the checked-in target at
`examples/3d_bench/cases/model-001/TARGET.md`, starts a local RSI experiment
with three prompt evolution rounds, lets the Task Agent produce `model.js`, lets
the Research Agent inspect saved evidence, and records the run under `.rsi/datasets/`
directory. The configured local model connections come from `.env`.

For a shorter end-to-end check, append `--iterations 1` to the command.
Resume with `--resume PATH`; the recorded iteration count is preserved.

This example keeps the generic RSI workflow visible while keeping private task
collections, internal repository URLs, and private evaluator data out of Git.
The generated model is checked using the local Contract V2 environment when
that environment has been prepared; this check is structural and is not a
visual-quality score.

## Public example layout

```text
examples/3d_bench/
  cases/model-001/TARGET.md
  local.yaml
  run_local.py
  adapter.py
  environment.py
```

`run_local.py` provides the shared application factory and standalone runner;
`adapter.py` owns task loading and scoring; `environment.py` owns the local
generation workspace and isolated validation. None of these modules is imported
by the infrastructure until the host explicitly registers this example.
