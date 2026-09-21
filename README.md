# AReaL-RSI

AReaL-RSI is a persistent multi-agent system for open-ended research. An Interaction agent
clarifies the task with the user, RSI plans and coordinates the work, and Research Agents run
experiments and report evidence. The workbench keeps the entire process visible and resumable,
including agent timelines, shared findings, jobs, budgets, verification results, and final output.

## Quick start

### Requirements

- Python 3.11 or later
- [uv](https://docs.astral.sh/uv/)
- Node.js 22.19 or later and pnpm when using the default `standard` agent backend
- A Responses-compatible model endpoint with tool calling for the `standard` and `codex` backends

The default macOS runtime uses `sandbox-exec`. On a trusted Linux server, set
`RSI_RUNTIME_ISOLATION=linux-unrestricted` explicitly. This runs agents with the service user's
permissions and does not provide filesystem or process isolation. See
[Agent backends](docs/agent-backends.md) for the full trust model.

### Install

```bash
uv sync --locked
cp -n .env.example .env
```

Configure the research model in `.env`. Credentials remain on the local machine.

```dotenv
RSI_BASE_URL=https://your-provider.example/v1
RSI_API_KEY=<your-api-key>
RSI_MODEL=gpt-5.6-luna
RSI_REASONING_EFFORT=xhigh
RSI_DATA_ROOT=.rsi/research-lab
```

## Choose a workflow

| Goal | Start with | Additional requirements |
| --- | --- | --- |
| Run a first complete research loop | [Arithmetic research](#run-your-first-complete-research-loop) | A research model and a target model |
| Create and monitor research interactively | [Web workbench](#web-workbench) | A research model |
| Optimize a real GPU kernel | [KernelBench](#kernelbench-passk-research) | SSH access and a deployed GPUQ |
| Run an existing frozen configuration | [Research CLI](#research-cli-and-http-api) | A `ResearchConfig` JSON file |

The [example catalog](examples/README.md) separates complete workflows from runtime profiles,
reusable components, benchmark assets, and deployment configurations.

## Run your first complete research loop

The built-in arithmetic workflow is the smallest end-to-end path. It requires only the research
model variables (`RSI_*`) and target model variables (`RSI_TARGET_*`). It does not require a GPU,
SSH, or EnvArena.

Prepare a frozen configuration, then run it through `ResearchApp`:

```bash
uv run --env-file .env --locked python -m areal_rsi.adapters.benchmark_research prepare \
  --benchmark math --directory .rsi/arithmetic/setup
uv run --env-file .env --locked rsi research \
  --config .rsi/arithmetic/setup/research.json --data-root .rsi/arithmetic
```

The command prints the run ID, status, and verification scores. If interrupted, use the printed
resume command. Complete records are stored under `.rsi/arithmetic/runs/<run_id>/`; open the
workbench with the same `--data-root` to inspect them visually.

This workflow covers candidate snapshots, TRAIN evaluation, selection, and an independent
VALIDATE final verification. See the [Arithmetic guide](examples/local/README.md) for its task and
verification contract.

## Web workbench

Start the workbench from the repository root:

```bash
uv run --env-file .env --locked rsi workbench --port 8197
```

Open **http://127.0.0.1:8197**, create a research session, and discuss the task, environment,
resource limits, and target with the Interaction agent. Once started, the task and collaboration
configuration are frozen, while progress and evidence remain visible throughout the run.

Use `--port 8200` for another port or `--data-root /path/to/data` for another data directory.
The workbench runs in the foreground and stops cleanly with Ctrl+C.

`rsi`, `rsi web`, and `rsi workbench` all start the same foreground workbench. New workbenches,
`rsi research`, and `rsi preflight` resolve their data directory in this order:
`--data-root`, `RSI_DATA_ROOT`, then `.rsi/research-lab`.

### Agent backends and collaboration

The workbench supports the `standard` backend, the `codex` backend, and trusted `runtime`
configurations such as Hermes or Claude adapters. Model settings and backend selection are frozen
when research starts. Every backend runs through `PackageAgentRuntime`; the runtime owns native
sessions, tools, and context compression, while RSI owns orchestration, permissions, jobs, and
verification.

In `passk` mode, RSI launches independent researchers that each solve the complete task. The run
stops when a formal result reaches the target or the group budget expires. Optional RSI
coordination adds evidence sharing and non-blocking guidance; new runs default to coordination
version v0.2.2, while v0.2.1 remains available for comparison. See
[pass@k](docs/passk.md) and [Collaboration](docs/collaboration.md).

The workbench renders one timeline per agent and compresses idle periods. It highlights score
improvements, reports, guidance, and active jobs. Evidence links show where shared information came
from without implying that the receiving agent adopted it. The view reads persisted history and
does not modify the collaboration state.

## KernelBench pass@k research

With access to the `et15` SSH host and a deployed GPUQ, start the default KernelBench research run:

```bash
uv run --env-file .env --locked python examples/kernelbench_artifacts/run.py
```

The launcher starts or reuses the workbench and runs four independent researchers on L3P10 with
the v0.2.2 coordination profile. Exploration runs for one hour with unlimited submissions. At the
end, the system freezes the best candidate, performs an independent final verification, and
archives the result. Closing the browser or launch terminal does not stop the background run.

The task launcher reuses a compatible workbench when one is already running. An unrelated service
on the selected port causes a clear error and is never stopped or replaced. Background logs and
PID files are stored under `<data-root>/runtime/`.

The [KernelBench guide](examples/kernelbench_artifacts/README.md) documents environment checks,
recovery, task selection, submission budgets, and recorded results. For the fixed three-task,
30-minute-per-researcher experiment, see
[KernelBench v0.2.1](examples/kernelbench_artifacts/v0.2.1/README.md). The
[generic harness workflow](examples/kernelbench/README.md) is a separate task that optimizes a
harness rather than a specific kernel.

## Research CLI and HTTP API

Run a prepared `ResearchConfig` without an Interaction conversation:

```bash
uv run --env-file .env --locked rsi research --config /absolute/path/research.json
uv run --env-file .env --locked rsi research --resume <run_id>
```

The CLI streams progress and preserves state when interrupted with Ctrl+C. Open the workbench with
the same data directory to inspect the run. While the CLI owns execution, the Web UI is read-only.
Use the workbench API when research must continue in the background while remaining interactive.

The local HTTP API uses a CSRF token. Read it from `GET /api/bootstrap`, then include it in the
`X-RSI-Token` header for write requests.

| Endpoint | Purpose |
| --- | --- |
| `POST /api/runs` with `{"message":"research request"}` | Create an Interaction conversation |
| `POST /api/runs` with `{"run_id":"id","config":{...}}` | Create a configured, idempotent run |
| `POST /api/runs/<id>/start` | Start a prepared run |
| `GET /api/runs` and `GET /api/runs/<id>` | Read runs, reports, jobs, and scores |
| `POST /api/runs/<id>/chat` | Send a user message |
| `POST /api/runs/<id>/pause`, `resume`, or `stop` | Control execution |

Write requests use a JSON body and the `X-RSI-Token` header.

## Other runnable benchmarks

Arithmetic and LiveCodeBench both use `ResearchApp`. Their operator adapters freeze the task set,
target model, scoring identity, and allowed changes before research starts.

- [Arithmetic quickstart](examples/local/README.md): three original TRAIN tasks and three
  independent VALIDATE tasks.
- [LiveCodeBench](examples/remote/README.md): a pinned EnvArena benchmark where researchers optimize
  a general system prompt.

See the [example catalog](examples/README.md) for more tasks, runtime configurations, reusable
components, and deployment assets.

## Validate the runtime

Run a production-path preflight before starting an expensive experiment:

```bash
uv run --env-file .env --locked rsi preflight --backend standard
uv run --env-file .env --locked rsi preflight --backend codex
# Validate an exact research configuration:
uv run --env-file .env --locked rsi preflight --config /absolute/path/research.json
```

Preflight uses the production runtime factory and checks the configured model, task tools, session
restart behavior, session identity, and duplicate-message receipts. The default timeout is 180
seconds and can be changed with `--timeout`. Results are written below `<data-root>/preflight/` and
do not create a research run or submit GPU work.

## Storage and recovery

Each run is stored under `RSI_DATA_ROOT/runs/<run_id>/`, including the task brief, sessions, jobs,
reports, shared evidence, frozen candidates, and verification results. RSI does not call models
while waiting for a job, report, or user response. Temporary API failures are retried.

After a service restart, active background runs and finalization resume automatically. Manually
paused runs remain paused. A run cannot modify candidates after final verification has completed;
start a new run for another experiment. Invalid external credentials and cluster failures remain
visible as explicit errors or waiting states.

All current entry points use `ResearchApp`. Removed TUI, MCP, fixed-round, and
`tui/run/resume/status/local-app` flows are not migrated. Existing records remain readable, but old
plans and checkpoints should not be resumed with current configurations.

## Documentation

| Guide | Contents |
| --- | --- |
| [Architecture](docs/architecture.md) | Component ownership, messages, candidates, and data flow |
| [Interfaces](docs/interfaces/README.md) | `ResearchConfig`, domain APIs, commands, and verification contracts |
| [Agent backends](docs/agent-backends.md) | Runtime configuration, isolation, tools, sessions, and testing |
| [pass@k](docs/passk.md) | Independent attempts, budgets, stopping, and experiment comparison |
| [Collaboration](docs/collaboration.md) | Guidance versions, permissions, prompts, and reproducibility |
| [Effective time](docs/effective-time.md) | Excluding API failures and GPU queue delays from active time |
| [GPU isolation](docs/gpu-queue-isolation.md) | KernelBench queue deployment and isolation boundaries |

## Development

Run the Python checks with `make check`. Run frontend checks with:

```bash
npm ci --prefix tests/frontend
npm test --prefix tests/frontend
```
