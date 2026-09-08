# Research workbench implementation

This is the implementation ledger for the end-to-end research workbench. The
existing fixed evolution workflows remain available. New research runs use the
same run-owned records and generic `ResearchIteration`, with no mandatory
baseline, solution, verification, or `EvolutionResult`.

## Delivery sequence

Each boundary receives its own local commits. Nothing is pushed automatically.
Named local branch checkpoints will make the eventual dependent PRs reviewable.

1. **Research records**: hard constraints and natural-language brief, assignments,
   seven-part reports, run-local evidence, immutable candidate snapshots and
   comparable verification records.
2. **Continuous sessions**: Responses transport, durable turns/tool calls,
   cancellation, compaction and recovery. Waiting makes no model requests.
3. **Collaboration**: Interaction / RSI / Research prompts, durable messages,
   autonomous RSI decisions, report-to-reply barrier, budgets and run controls.
4. **Execution**: background jobs, resource allocation, cancellation, restart
   reconciliation and trusted verification commands.
5. **Examples**: KernelBench harness search and CUHK-X training search, including
   simulated GPU jobs and documentation for replacing them with real resources.
6. **Workbench**: local chat, requirements, plan, search graph, reports, evidence,
   scores, jobs and controls, followed by browser interaction and visual checks.
7. **Integrated acceptance**: deterministic failure/recovery coverage, real model
   behavioural mock runs for both examples, actual browser testing, final docs.

## Acceptance requirements

- Interaction preserves both machine-enforced constraints and a natural-language
  brief; the original user messages stay with the run.
- RSI chooses its own research sequence and agent count within limits, can search
  papers itself, and can continue useful work while researchers execute.
- A research direction retains its logical session and workspace. A completed
  report ends execution until RSI explicitly replies. Waiting consumes no tokens.
- Nodes archive task, concrete execution evidence and an intelligible report;
  nodes without candidates or verification remain valid.
- Messages, decisions, sessions, evidence, shared experience, jobs, baseline,
  best and final result all belong to their run. Late/duplicate messages cannot
  restart stopped agents or repeat completed work.
- Only immutable candidates evaluated under the same trusted contract can become
  best. Missing scores remain null; diagnostic/mock results are labelled.
- KernelBench uses the target model and allowed harness edit scope. CUHK-X uses
  training/design candidates, subject-separated local validation and public
  competition constraints, with the user's confirmed organizer authorization.
- GPU work can be simulated now and switched to documented real commands later.
  Simulation is never evidence of real benchmark or competition performance.
- Web interaction and graph rendering are tested in a real browser, including
  waiting, no baseline, failure, multiple branches, refresh, pause and resume.
- Existing relevant tests, lint and type checks pass, or an unchanged pre-existing
  failure is reproduced and explicitly recorded.

## Current evidence

- Starting point: clean `zwh/dev` at `b97fa80`.
- Baseline implementation and previous validation: see the final section of
  [the detailed design](rsi-research-implementation.md).
- No new implementation or acceptance claims should be inferred from this plan.
