# Single-Agent Core

```text
External harness → plugins/areal-rsi (Skill + MCP configuration)
        └── ui/mcp.py → infra/service/transport.py → service/handler.py → SessionSystem

ui/dsh upstream components ← main.mjs ← ui/tui.py, ui/cli.py
        │ calls; consumes SessionEvent
        ▼
infra/session.py · SessionSystem
        ├── config.py                     Configuration and model catalog
        ├── coordination/store.py         Atomic state, session ownership lock, UI records
        └── coordination/runtime.py · SessionRuntime
                ├── policies/goal.py      Phase transitions and Goal progression
                ├── tools/control.py      Three Agent control tools and role authorization
                ├── policies/prompts.py   Role, handoff, continuation, and recovery prompts
                ├── ../experience.py      Local documents and independent summarization
                └── ../codex.py           Native app-server JSON-RPC
                        └── Codex         Model, tools, history, compaction, sandbox
```

`api/` defines named data contracts and protocols without depending on implementations; `infra/` does not depend on UI.
Sessions without a bound problem use a single Agent. SessionSystem manages optional ResearchProblem bindings;
ProblemRun in infra/coordination manages candidates, plans, evidence, and frozen selections, while policies/selection defines comparison rules.
TIR and Search provide domain data, workers, and plans through three problem interfaces, reusing execution, training,
evaluation, and trajectories. Agent tools for bound problems live in coordination/tools; only the host invokes final evaluation.
Binding a problem adds neither session roles nor a second model context. See the [problem API](interfaces/README.md) for integration and commands.

The remaining infrastructure modules are grouped by responsibility:

```text
infra/
├── session.py                    Public session operations
├── config.py                     Configuration and model catalog
├── codex.py                      Native Codex process and JSON-RPC connection
├── experience.py                 Experience catalog, curation, and document publication
├── service/                      External request handling and local transport
├── coordination/                 Session runtime, lifecycle records, and research coordination
│   ├── policies/                 Goal/selection decisions and role prompts
│   ├── tools/                    Agent control tools and authorization
│   └── evaluation/               Research evaluation and evidence comparison
├── execution/
│   ├── jobs.py                   Local/Slurm job lifecycle and GPU admission
│   ├── training.py               AReaL worker plans and checkpoint validation
│   └── worker.sh                 Shared GPU container startup
└── storage/
    ├── artifacts.py              Atomic records, content references, and source snapshots
    └── trajectories.py           Append-only trajectory index
```

Research coordination calls execution and storage; execution uses storage for durable receipts.
Neither package depends on session orchestration or the service. Storage does not launch jobs or choose candidates.
Session lifecycle records remain in `coordination/store.py`, alongside their owner and recovery rules.
The short, cohesive Codex and experience modules remain individual files.

`SessionSystem` manages sessions and user operations: model settings, messages, entering RSI, pausing, and resuming.
Each open session has one `SessionRuntime` owning its runtime state, lock, Codex connection, and execution loop.
Public operations validate and update state under that lock. Runtime handles connection recovery, turn delivery,
events, and resource cleanup without depending on SessionSystem. `goal.py` only decides phase transitions and
Goal progression; it does not call Codex or access persistent storage.

## Plugin Boundary

`infra/service/transport.py` owns local Unix-socket communication and background process startup and shutdown,
with one service per data root. `service/handler.py` dispatches validated requests, deduplicates writes,
and owns asynchronous request tasks and SQLite receipts. It calls SessionSystem for session operations,
reusing the existing Goal and Agent scheduling.
`ui/mcp.py` generates tools from `api/control.py` and forwards requests; CLI `call` uses the same service and contracts.
The external harness hands off a summary, relays messages and controls, and observes results.
RSI's own Codex runtime retains ownership of the internal native threads.

Each write request has a stable request_id; duplicates return the same receipt. Uncertain requests interrupted by
service termination become interrupted and are never replayed automatically. Resuming work requires explicit resume.
Request receipt recovery stays in the handler; `SessionSystem.recover_interrupted(run_id)` handles abandoned
session state under its ownership lock. It pauses unfinished work and expires questions and interrupted curation,
without connecting to Codex. Active turns remain recorded for reconciliation when the session is explicitly opened.
Observers independently read complete messages using transcript byte cursors, excluding tool logs by default.
State and current questions are returned separately without relying on lossy deltas. The live UI queue is bounded.
Questions persist in the session record for reconnecting observers, but rebuilding a native connection invalidates old prompt_id values.

The TUI and original run command still embed SessionSystem and pause work on exit; the plugin uses the background service.
Both share the core, native runtime, and persistence format. The existing owner.lock prevents simultaneous ownership
of the same run; the service does not take over sessions currently owned by the TUI.

## Session Flow

1. `create(config)` only saves a regular session. `open(run_id)` connects to Codex using a separate configuration directory.
2. `send_message()` starts a native turn; follow-up messages during execution use `turn/steer`.
3. `/rsi` waits for the current turn to finish, then switches to Interaction in **the same thread** to continue the discussion.
4. Interaction saves a `TaskBrief` with `rsi_set_task`. When the user requests execution, it calls
   `rsi_start_goal`; the user can also hand off a prepared task with `/start`.
5. After the current turn fully ends, `thread/fork` copies the native history to Leader.
   Leader is the sole executor: it reads code, makes changes, calls tools, and verifies results itself.
6. Leader saves progress with `rsi_update_goal`. If the turn ends while the goal is still active, the host starts another turn.
7. `/chat` returns to regular work in the current Leader thread, retaining the context of the work just completed.

Roles are phases of the same Codex core, without an additional Agent forwarding layer. Execution role prompts live in
`infra/coordination/policies/prompts.py`, along with handoff, continuation, and recovery prompts. The summarization role lives in `infra/experience.py`.

New sessions default to `danger-full-access` / `never`: the working directory is a starting location, not a read/write boundary; operations are limited by the current OS user's permissions.
All phases and summarization inherit the same configuration. Resumed sessions retain their saved permissions. Prompts and RSI control-tool authorization define role responsibilities.

## Context Ownership

Codex stores user and assistant messages, tool calls and results, image inputs, and compaction records. RSI does not rebuild message arrays,
truncate history to the last few records, or replace native history with task configuration or the UI transcript.
Role instructions are updated through each turn's application context; native Codex base instructions remain in place.
The Leader fork creates a new thread that retains the source session's existing context; subsequent work continues on that branch.

The model's context window still applies. Long sessions use Codex's own compaction results; this does not mean all original text,
tool output, and image pixels remain in the model input forever. Original file/image paths and key domain constraints should be saved
in task configuration so they can be read again when needed. RSI does not implement a second session compaction mechanism; experience documents contain reusable methods across tasks and do not replace native context.

## Goal Stop Conditions

- Leader submits `completed` with nonempty verification evidence, and the current turn ends normally: complete.
- Leader submits `blocked` with a question: wait for the user, then continue after an answer.
- The user invokes pause/stop or exits the TUI: interrupt and save. Closing a plugin observer leaves background work running.
- A user-defined token or execution-time budget is exhausted: stop; adjust or remove the budget, then use `/resume`.
- A native turn fails or disconnects: save the uncertain execution state and wait for explicit recovery.
- Two consecutive automatic continuations use no work tools: wait for the user to avoid spinning without progress.

New goals have no token or time budget by default. Interaction only saves the task; the user controls budgets through `/budget`.
Budget adjustments retain usage already incurred. Clearing `budget_limited` leaves the session paused until an explicit `/resume`.
Token usage is counted from the start of Leader using differences in the cumulative usage reported by Codex, including input context.
The limit triggers interruption after a usage notification arrives; a single model call can exceed the remaining allowance.
In this version, `max_wall_seconds` counts Leader's active execution seconds, excluding pauses and time spent waiting for the user.
For ordinary Goals, Leader submits completion evidence and the host validates its structure. Bound experiment tasks also require validation of actual candidate evidence and the frozen selection;
session completion, final test acceptance, and achievement of the primary metric target are recorded separately.

## Recovery and Persistence

```text
RSI_DATA_ROOT/
├── service/                      Service lock, logs, and requests.sqlite3 receipts
├── codex/                        Separate CODEX_HOME; native history and configuration
├── experience/<topic>.md         One document per topic, updated in place
├── experience.lock               Summary write lock, outside the experience directory
└── sessions/<run_id>/
    ├── session.json              Sole lifecycle state; replaced atomically
    ├── owner.lock                One host per session
    ├── transcript.jsonl          UI display, not model context
    ├── learning.log              Independent summarization process diagnostics
    └── codex.log                 Native process diagnostics
```

Input intent is recorded before delivery; control-tool results are persisted before replying. Repeated control calls reuse receipts by call ID,
and changed arguments are rejected. Recovery checks the result of the last native turn; commands are not automatically replayed when delivery is uncertain.
`/resume` instructs the Agent to inspect history, files, and processes before continuing unfinished work. This cannot guarantee exactly-once
execution for arbitrary external side effects, but prevents the host from blindly retrying when the outcome is unknown.

Additional UIs should reuse `SessionSystem` control methods and its event stream.
Local experience evolution uses independent summarization sessions; Yuque synchronization is not implemented.
`/learn-mode` chooses between automatic summarization after completion and waiting for human feedback. Waiting consumes no model calls and does not change the task's completed status.
The summarization session receives the latest catalog and the latest body of the topic from the previous summary; the model decides whether to merge or create a document based on applicability and method.
For the same topic, it returns a complete, deduplicated body and preserves the slug; identical content is not written again. The body has a hard limit of 4,000 characters.
The experience directory contains only regular `experience/<topic>.md` files, without historical versions, hash-based names, or symlinks.
Regular work and Leader refresh the experience catalog each new turn and read the latest bodies as needed; previous conversation content remains available.
Interaction receives no new experience catalog and must not turn experience suggestions into user constraints. The original domain context from earlier conversation remains intact.
Saving acquires a lock and compares against the original content captured when summarization began, preventing concurrent summaries from overwriting other changes; conflicts require another summary.
The body is written to a temporary file and then replaced atomically. Locks and temporary files stay in the data root, outside the experience directory.

System idle sleep is inhibited during execution and released when stopped or waiting. Goal continuation follows
[Codex Goals](https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex),
and the sleep-inhibition scope follows native Codex's sleep-inhibitor. The plugin uses a local background service;
cross-machine recovery is not implemented.

## Source Reading Guide

Start with the session and task contracts in `api/research.py`, then follow the normal
`create → open → send_message → enter_rsi → start` path in `infra/session.py` before examining pause and recovery.
Goal policies and tool authorization live in `coordination/policies` and `coordination/tools`, respectively;
experience reading, curation, and concurrent writes live in `infra/experience.py`.
`ui/cli.py` and `ui/tui.py` only handle entry points and event display. See [third-party notices](../THIRD_PARTY_NOTICES.md)
for terminal components, patches, and their sources; UI tests verify the original file hashes.
