# Research workbench delivery ledger

Implemented on `zwh/dev`, starting at `b97fa80`. All changes are local commits;
nothing has been pushed. The original fixed evolution workflows remain available.
The open research entry uses generic nodes and does not require a baseline,
Solution, verification, or `EvolutionResult` for every task.

## Delivered

| Area | Implementation |
| --- | --- |
| Records | Hard limits plus free-text brief; assignments, concrete reports, run-owned evidence, immutable candidates and comparable verification records |
| Sessions | Responses API, durable messages and tool effects, continuous per-direction context, safe compaction, explicit bounded retries; waiting makes no model requests |
| Coordination | Separate Interaction / RSI / Research prompts; autonomous research planning, parallel directions, report/reply barrier, shared evidence, stop/pause/resume and global budgets |
| Execution | Background jobs, whole-job GPU allocation across runs, cancellation, restart reconciliation, frozen verifier inputs and trusted result validation |
| Examples | KernelBench fixed-model harness execution with development/search/final pools; CUHK-X training, anonymous inference and host scoring; simulated jobs plus real configuration preflight |
| Workbench | Local chat and brief, plans, search graph and node details, reports, shared evidence, jobs, scores, budgets, retries and candidate ZIP export |
| Entry points | `rsi workbench`, `rsi research`, real API preflight, operator config generator and reproducible CPU bridge smoke |

## Commit boundaries for subsequent PRs

The history intentionally preserves small commits. Reviewable groups and their
main commits are below; later integration fixes depend on the earlier modules.
Before opening PRs, choose a dependent PR chain or cherry-pick the relevant fixes
into topic branches. Do not submit this complete branch as one large PR.

| Group | Main commits |
| --- | --- |
| Research records | `9baddb3` |
| Continuous Responses runtime | `71b92a1`, `e930f23` |
| Agent roles and coordination | `8b8ad96`, `3024c6a` |
| Background execution and resource recovery | `aa06de8`, `b2c0e68` |
| CUHK-X adapter and trusted verification | `1423201`, `4581e6d`, `8ec76a0`, `4e13dd9`, `99df3c9` |
| Web research and evidence | `7b44a0f`, `a368a2a` |
| KernelBench execution adapter | `913378f` |
| Workbench and application assembly | `b20475b`, `5c16354`, `e581567` |
| Real configuration, CLI and acceptance fixes | `25222eb`, `e182382`, `d857d22`, `5409f9f`, `b1c40d8`, `251828f`, subsequent stop-run and documentation commits |

## Validation and practical limits

Detailed outcomes, run IDs, observed agent behaviour and artifact locations are
recorded in [the acceptance report](research-acceptance.md). The report preserves
failed rehearsals alongside successful ones rather than retroactively changing
budgets or simulated outcomes.

- Real Astra/xhigh API preflight covered tool calling, remembered context and
  duplicate-message handling.
- KernelBench completed a real-model workflow rehearsal with simulated Task Agent,
  GPU work and scores. CUHK-X rehearsals covered real public-paper retrieval,
  parallel research, shared evidence and explicit report replies.
- A separate CUHK-X CPU smoke exercised actual PyTorch training, checkpoint
  collection, search/final scoring, candidate selection and ZIP export using
  synthetic data, without model calls or GPUs.
- Real Chrome tests exercised the workbench at desktop and mobile sizes, graph
  navigation, chat, lifecycle controls, retry, reload, disconnect and export.
  An actual Interaction Agent conversation also updated a brief without starting
  research prematurely.
- Automated coverage includes resources, recovery, isolation boundaries, frozen
  verification identities, sessions, coordination, CLI and UI. The acceptance
  report records exact final results and the pre-existing macOS sandbox failure.

GPU and competition-data validation remain dependent on the user's resources.
The CPU smoke does not validate Docker/CUDA. KernelBench's upstream same-process
kernel evaluator is not complete isolation against malicious generated Python;
its current Task executor supports a Mac controller with an SSH Linux GPU runner.
Simulation scores are only workflow fixtures and never benchmark claims.

## Where to start

- [Run the workbench and prepare real execution](research-workbench.md)
- [Concise design](rsi-research-design.md)
- [Detailed implementation reference](rsi-research-implementation.md)
- [Acceptance evidence and remaining limitations](research-acceptance.md)
