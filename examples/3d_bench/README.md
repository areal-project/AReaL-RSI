# Local 3D RSI example

This directory contains one public example task, `MODEL-001`. It is a small
adapter example for the generic RSI interfaces; it is not the private 100-task
benchmark or its evaluator.

## Run locally

From the repository root:

```bash
uv run --env-file .env --locked python examples/3d_bench/run_local.py
```

The script reads the checked-in target at
`examples/3d_bench/cases/model-001/TARGET.md`, starts a local RSI experiment
with three prompt evolution rounds, lets the Task Agent produce `model.js`, lets the Research Agent inspect
the saved evidence, and records the run under the ignored `.rsi/datasets/`
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
  run_local.py
```

The remaining implementation modules are temporary compatibility code for the
current development branch. They are not part of the public task data and will
be removed or moved behind the Benchmark runtime boundary in a later cleanup.
