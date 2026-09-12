# Coordination boundaries

`Coordinator` implements run and agent state transitions without making model
requests. `ResearchActions` exposes role-scoped tools; `ResearchEngine` drives
continuous model sessions. Execution and web research tools are separate adapters.

- Before start, Interaction can refine both the natural brief and structured
  limits. Starting freezes the comparison contract and hard limits.
- RSI can research itself, spawn directions, reply to reports, publish evidence
  and stop directions. Researchers cannot invoke those coordination actions.
- RSI's `stop_run(reason)` ends research when evidence does not justify further
  work, including when the target remains unmet. It uses `Coordinator.stop`,
  cancels directions and background work through the existing engine/execution
  lifecycle, clears pending user questions and archives an RSI-to-user explanation.
  The status remains `stopped` (`RunState.CANCELLED`), never `succeeded`; existing
  candidates, best/final references and scores are preserved. `finish` remains the
  successful completion path and continues to enforce the required target.
  If the latest verification is `infra_error`, `stop_run` is rejected: RSI must
  diagnose the issue or use `ask_user` and wait. `execution_blocker` in the state
  summary identifies this condition. An explicit user stop remains available.
- Every assignment becomes a generic `ResearchIteration`. Completion requires a
  concrete `ResearchReport`; products and verification are optional.
- A report immediately stops the model turn. A normal final reply also becomes a
  report. RSI's next assignment resumes the same session and workspace; the
  coordinator links the latest report without requiring the model to copy its ID.
- A background job can wake the same assignment without creating another node;
  this is separate from the report-to-RSI barrier. `wait_for_job` durably subscribes
  its caller, including a researcher observing RSI's verification job. Later messages and
  free-form `wait_reason` changes do not cancel that subscription. Completion is
  delivered once per subscriber; a processed result with a missing notification
  is reconciled on restart. Finished assignments and stopped directions are not
  resumed by late job results.
- `run_job` and `verify_candidate` accept `wait=true`: a short job returns directly,
  otherwise its caller durably sleeps and resumes with the published feedback.
  No model request is used to poll. Archived calls without `wait` stay asynchronous.
  Optional `ApprovedCommand.feedback_file` defaults to `feedback.json`; bounded
  excerpts are archived once. Ordinary feedback is visible to its owner and RSI;
  verifier feedback only comes from the configured public output directory.
- `rerun_job` freezes the original execution's inputs, parameters and command into
  a new, normally budgeted job. It rejects running jobs and changed command templates;
  replaying the same tool call remains idempotent. `collect_job_output` with
  `as_candidate=true` snapshots published artifacts directly; an empty destination
  avoids copying them into a mutable working directory.
- Job submission receipts, `job_status` and completion messages include the command
  name, caller-supplied `parameters`, iteration and candidate identity, plus the
  verification phase when applicable. Use these fields and the actual feedback to
  identify the task; a job ID or its owner's direction is not a task label. Parameters
  are public run data: credentials belong in the configured execution environment.
  Operator argv, environment and private verifier inputs are not included. Legacy
  jobs without saved parameters return `null` rather than inferred values.
- Researcher turn and recovery state includes `current_assignment` with its ID,
  instruction and completion criteria. A job completion message remains a delivery
  within that assignment; it does not replace the task or authorize another attempt.
  New assignments update this state from their durable records. Later RSI messages
  can refine the task as usual.
- Material discovery identifies output excerpts by job and file, and web searches
  by query. If an older excerpt has no timestamp, `job_created_at` supplies its
  accessible source job's date for sorting; the archival date remains unknown.
  Summaries stay bounded, omit full output bodies and respect job access controls.
  Historical records are not rewritten to populate the index.
- Messages and tool effects have stable identities. Recovering a delivery reuses
  the same session turn and reconciles the same effect; completed effects return
  their original result. A stopped direction archives late reports without wakeup.
- Registered researchers' messages can reach an active RSI session at a resolved
  tool boundary. The runtime persists each message before acknowledging it, so it
  will not become a redundant later turn. This does not let researchers steer
  other researchers or Interaction. RSI replies to users retain the actual user
  message as their reply target, even if a research report arrived more recently.
- Incremental event reads skip acknowledged files before decoding their bodies.
  A full-history request still reads the complete archive; subsequent workbench
  polls only decode events after the supplied cursor.
- Waiting has no active inference. RSI can still perform useful work while other
  directions run; only an explicit wait or completed turn yields control.
- Outstanding report replies are derived from existing researcher/node state and
  included in RSI's model-facing state. If RSI implicitly becomes idle after a
  delivered report without assigning or stopping its researcher, the scheduler
  sends one durable reminder for that report. Explicit waits (including user/job
  waits), paused/stopped runs and failed controllers are respected. The reminder
  does not start a research iteration; its stable identity prevents polling or
  restart loops. An unanswered reminder remains visible as an outstanding reply.
- Event writers use a run-local process lock and a recoverable sequence index.
  Committed events precede index updates, so a restart discovers an unindexed
  tail. Indexed appends and incremental reads do not scan the historical directory;
  legacy archives bootstrap from their existing immutable event filenames.
- Pause preserves unfinished deliveries for recovery. A stopped, previously
  started research run can be reopened explicitly by the user via `resume`.
  It retains scores, reports, candidates and sessions, restarts the controllers,
  and makes reported directions stopped by the run available for RSI assignments.
  Individually stopped directions and cancelled unfinished work remain archived;
  reopening does not replay completed jobs. A failed agent needs an explicit retry.

All messages, effects, plans, events and session data live below their run. Raw
session content and private verifier files are not public research records.
Research tools validate references against the actor's own or explicitly shared
evidence. Legacy workspace file tools enforce their workspace boundary; native DSH
file/shell tools use the host account's permissions. These checks do not replace
process/container isolation for executed code.

Role prompts live in `agents/interaction/prompt.py`, `agents/rsi/prompt.py` and
`agents/research/prompt.py`. They preserve the autonomy to choose research order
and methods, while requiring concrete assignments, bounded reports and evidence
for claimed results. They do not prescribe research task categories.

Model-facing state, tool visibility and published verifier feedback are described in
[the simplification design](../research-simplification.md).
