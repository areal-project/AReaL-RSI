# Research run records

`api/research.py` contains records used by the open research coordinator. Existing
Harness / Artifact evolution records remain supported.

- `TaskBrief` stores the objective, full natural-language instructions, hard limits
  and an optional verification contract. These are complementary representations.
- `Assignment` records RSI's concrete instruction and completion criteria.
- `ResearchReport` records task completion, actual work, problems, actual results,
  interpretation, next steps and shared findings. Details are linked as evidence;
  a report need not contain a candidate, score or `EvolutionResult`.
- `ResearchIteration` still links the assignment, report and arbitrary evidence.
- `CandidateSnapshot` is an optional immutable file snapshot. It does not require
  a Task Agent, a model/harness combination or a `SolutionVersion`.
- `VerifiedResult` binds a candidate to a fixed contract, execution status, phase
  and metrics. A failed execution has no score. Only successful, passing search
  results under the same contract and simulation mode participate in selection.
- `EvidenceRecord` retains authorship, references, limitations and corrections.
  Publishing creates a new shared record, preserving the original claim.

`ResearchStore` uses `FileRunStore` for run and node records. Briefs, assignments,
reports, candidates, verification records, evidence and atomic numbered events
live under `runs/<run_id>/`. Immutable file bodies reuse the existing content store.
Run records contain nullable brief, baseline, best and final references. The first
successful candidate never silently becomes the initial baseline.

Candidate creation checks actual file additions, changes and deletions against
the user's allowed paths relative to the supplied parent snapshot. The coordinator
is responsible for choosing a legitimate parent and verifying the final candidate
against the run's original edit boundary. The operator may import a complete seed.

Public record resolution rejects other runs, private verifier records and agent
session directories. The coordinator must additionally enforce role-specific
evidence sharing; a URI alone is not authorization. These filesystem interfaces
do not constitute an execution sandbox.
