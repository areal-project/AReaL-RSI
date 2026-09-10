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
- Messages and tool effects have stable identities. Recovering a delivery reuses
  the same session turn and reconciles the same effect; completed effects return
  their original result. A stopped direction archives late reports without wakeup.
- Waiting has no active inference. RSI can still perform useful work while other
  directions run; only an explicit wait or completed turn yields control.
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
