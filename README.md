# AReaL-RSI

AReaL-RSI is a persistent research system. In the default `goal` mode, one **Leader executes the
work directly**: investigating, editing, running experiments, checking evidence, and accepting the
result of a run. When a model turn ends with an active goal, the scheduler continues the same
session. An optional Interaction agent helps clarify requirements before execution starts.

| Mode | Execution | Run completion |
| --- | --- | --- |
| `goal` (default) | One Leader; no Researcher is created | The Leader accepts the run against its criteria and evidence |
| `legacy_passk` | Fixed k independent Researchers, each attempting the complete task | A supervisor maintains the attempts; the system stops at the target or configured budget and performs final verification when configured |

Legacy pass@k preserves the v0.1 behavior from `main` at `4771407`. Future Agent Team work will let
the Leader delegate portions of a task to Researchers while retaining responsibility for integration
and run acceptance. Only the contracts exist today; there is no runnable team mode.

## Quick start

Requirements: Python 3.11 or later and [uv](https://docs.astral.sh/uv/). The default `standard`
backend also needs Node.js 22.19 or later and pnpm. The `standard` and `codex` backends need a
Responses-compatible model endpoint with tool calling. See [Agent backends](docs/agent-backends.md)
for configuration, isolation, and optional adapters. macOS uses `sandbox-exec` by default;
Linux uses unrestricted execution with the launching user's permissions.

```bash
uv sync --locked
cp -n .env.example .env
# Set RSI_BASE_URL, RSI_API_KEY, RSI_MODEL, and other model settings in .env.
uv run --env-file .env --locked rsi tui
```

The terminal UI is the default entry point; `uv run --env-file .env python run.py` starts it too.
Enter a goal, clarify its acceptance criteria with Interaction, and hand it to the Leader.

For a small task that requires only the research model, load the CSV-script example and enter
`/start` in the TUI:

```bash
uv run --env-file .env --locked rsi tui --config examples/goal.json
```

To work on an existing Git repository:

```bash
uv run --env-file .env --locked rsi tui --workspace /absolute/path/to/repository
```

The Leader gets a separate worktree from the repository's HEAD at startup. Uncommitted changes in
the user's checkout are not included. The TUI shows the workspace path. For ordinary files, use
`seed_dir` in a configuration to copy starting material into the Leader's workspace.

## Choose a workflow

| Goal | Start with | Additional requirements |
| --- | --- | --- |
| Try a single-agent task | [Goal example](examples/goal.json) | A research model |
| Optimize a harness with formal evaluation | [Arithmetic](examples/harness/README.md#算术-harness) | A target model in addition to the research model |
| Optimize a LiveCodeBench system prompt | [LiveCodeBench](examples/harness/README.md#livecodebench-harness) | EnvArena access and a fixed benchmark |
| Optimize a reusable GPU kernel harness | [KernelBench harness](examples/harness/kernelbench/README.md) | KernelBench data and a GPU evaluation environment |

The [example catalog](examples/README.md) separates runnable workflows, backend profiles,
components, and deployment assets.

### Arithmetic research with formal verification

Configure research model variables (`RSI_*`) and target model variables (`RSI_TARGET_*`) in `.env`,
then prepare and run the built-in arithmetic workflow. It needs no GPU, SSH, or EnvArena.

```bash
uv run --env-file .env --locked rsi prepare arithmetic --directory .rsi/math/setup
uv run --env-file .env --locked rsi research \
  --config .rsi/math/setup/research.json --data-root .rsi/math
```

This generates a `ResearchConfig` for `ResearchSystem`. The Leader improves the task harness,
compares candidate snapshots on TRAIN, verifies the selected candidate independently on VALIDATE,
and accepts the run after checking the result. See the [harness guide](examples/harness/README.md)
for the task and verification contract.

## Interaction and recovery

| Input | Behavior |
| --- | --- |
| Ordinary text | Clarify requirements with Interaction before startup; send input directly to the Leader during execution |
| `/start` | Start execution using the saved requirements |
| `/pause`, Ctrl+P | Pause model execution; submitted jobs remain managed |
| `/resume` | Resume a paused run; an exhausted budget must be addressed first |
| `/retry` | Retry the failed current role using the existing task and session |
| `/stop` | Terminate the run and cancel unfinished managed jobs |
| `/export /new/path` | Export the accepted, immutable candidate to a new directory |
| `/new` | Create a new task after the current run ends |
| `/quit`, Ctrl+Q | Save and exit; retain state and jobs for recovery |

Reopen a run using its original data directory:

```bash
uv run --env-file .env --locked rsi tui --resume <run_id>
```

Data directory precedence is `--data-root`, `RSI_DATA_ROOT`, then `.rsi/research-lab`. The TUI manages
one run at a time, and only one process may control a run. Completed runs can be inspected and
exported; additional work requires a new run. Closing the TUI interrupts local model execution
while preserving the active message, session, and managed jobs for recovery.

## Headless execution and runtime checks

```bash
uv run --env-file .env --locked rsi research --config examples/goal.json
uv run --env-file .env --locked rsi preflight
```

Headless execution prints JSON events. When operator input is required, it exits with code 3 and
prints a TUI resume command. Preflight checks the configured backend, tool calls, and session
recovery without submitting benchmark or GPU jobs.

A general task without a formal verifier can complete on the Leader's recorded criteria and
evidence. When a verifier is configured, acceptance requires formal verification of the same
candidate under the current protocol. A textual claim or another candidate's score cannot replace
that result. Pausing, reaching a budget, and waiting for input do not mean that the goal was met.

## Compatibility and development

Only `goal` and `legacy_passk` are supported. The Web workbench, older collaboration variants, and
historical experiment launchers have been removed. Old run records and configurations are not
migrated. Run acceptance does not automatically merge, publish, or deploy a result.

```bash
make check
```

`infra/research.py` exposes `ResearchSystem`, the application entry point. `api/` defines contracts,
`infra/` implements execution, storage, evaluation, and coordination, and `ui/` handles presentation
and input. `make check` runs Python behavior and architecture tests, Ruff, and Mypy.

| Guide | Contents |
| --- | --- |
| [Architecture](docs/architecture.md) | Responsibilities, goal lifecycle, and future Agent Team boundaries |
| [Interfaces](docs/interfaces/README.md) | Domain APIs, controls, and verification contracts |
| [Legacy pass@k](docs/passk.md) | Independent attempts, budgets, stopping, and final verification |
| [Agent backends](docs/agent-backends.md) | Runtime configuration, isolation, tools, and sessions |
| [Effective time](docs/effective-time.md) | Accounting for API outages and GPU queue delays |
