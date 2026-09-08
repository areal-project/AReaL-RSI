# Coordination boundaries

`Coordinator` implements run and agent state transitions without making model
requests. `ResearchActions` exposes role-scoped tools; `ResearchEngine` drives
continuous model sessions. Execution and web research tools are separate adapters.

- Before start, Interaction can refine both the natural brief and structured
  limits. Starting freezes the comparison contract and hard limits.
- RSI can research itself, spawn directions, reply to reports, publish evidence
  and stop directions. Researchers cannot invoke those coordination actions.
- Every assignment becomes a generic `ResearchIteration`. Completion requires a
  concrete `ResearchReport`; products and verification are optional.
- A report immediately stops the model turn. Only an explicit reply to that exact
  report can issue the next assignment. The session and workspace are reused.
- A background job can wake the same assignment without creating another node;
  this is separate from the report-to-RSI barrier.
- Messages and tool effects have stable identities. Recovering a delivery reuses
  the same session turn and reconciles the same effect; completed effects return
  their original result. A stopped direction archives late reports without wakeup.
- Waiting has no active inference. RSI can still perform useful work while other
  directions run; only an explicit wait or completed turn yields control.
- Pause preserves unfinished deliveries for recovery. Stop is terminal. A failed
  agent needs an explicit retry; its active delivery remains available for recovery.

All messages, effects, plans, events and session data live below their run. Raw
session content and private verifier files are not public research records.
Research tools validate references against the actor's own or explicitly shared
evidence. Workspace file tools cannot escape into another agent or host directory.
These access checks do not replace process/container isolation for executed code.

Role prompts live in `agents/interaction/prompt.py`, `agents/rsi/prompt.py` and
`agents/research/prompt.py`. They preserve the autonomy to choose research order
and methods, while requiring concrete assignments, bounded reports and evidence
for claimed results. They do not prescribe research task categories.
