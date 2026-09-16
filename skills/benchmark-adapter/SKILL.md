---
name: benchmark-adapter
description: Integrate benchmarks and trusted evaluators into AReaL-RSI, including research-workflow commands, legacy BenchmarkBundle adapters, and stateful task sequences. Use when adding or reviewing benchmark execution, scoring, or data-provider integrations.
---

# Adapt A Benchmark

Integrate the requested Benchmark without coupling it to an RSI optimization target. A Benchmark
defines what is evaluated; `ExperimentPlan.target` independently determines whether an experiment
changes a Harness, Model, or Artifact.

## Repository Architecture Boundary

In this repository, `src/areal_rsi/` is benchmark-independent infrastructure. Put new
benchmark-specific runners, graders, task/split handling, protocol checks, configuration
generators, prompts and launch instructions in `examples/<benchmark>/` (or the dedicated
benchmark directory selected by the user). For example, PAST-Bench lives in
`examples/past_bench/run.py`. An existing benchmark-specific module under `src/` is legacy
placement, not a precedent for new integrations; do not migrate unrelated legacy code unless requested.

Dependencies must point from the benchmark integration to infra, never the reverse. Infra
may define reusable configuration fields, scheduling, candidate snapshots, approved commands
and trusted result identities. It must not import a benchmark implementation or hard-code its
name, default path, model, split, score formula or runtime in TUI/MCP routing, persona text,
configuration loading, coordination or execution. A module named "bridge" or "adapter" is
not generic if it still contains those assumptions.

Connect through operator-owned `ResearchConfig` and `ApprovedCommand` values. If a benchmark
requires startup checks, supply a bounded `preflight` command from its own directory; the
generic bridge checks its returned `verification_identity` against the configured identity.
Keep benchmark-specific credential and dependency checks in that command. Store one-sentence
launch examples in benchmark documentation, not benchmark branches in the infra persona.

Before delivery, search changed infra files for the benchmark name, imports and paths; inspect
each match for coupling. Test the generic entry with a benchmark-independent fixture, and the
integration through its actual external script. Update entry paths, documentation, and local
prepared configurations together. Moving code changes protocol identity when source hashes are
pinned: regenerate new locks/configurations without rewriting historical experiment evidence.

## Choose the Integration Surface

The current research workflow uses operator-owned `ApprovedCommand` evaluators, not
necessarily the legacy BenchmarkBundle catalog. Before choosing an interface, read
`src/areal_rsi/coordination/config.py`, `src/areal_rsi/execution/models.py`,
`src/areal_rsi/coordination/verification.py` and the closest maintained example.
`examples/past_bench/` demonstrates a self-contained trusted harness integration. KernelBench
also demonstrates evaluation contracts, but its legacy module placement is not the desired boundary.

For research integration, produce `evaluation.json` from the trusted evaluator and pin
comparison identities in `metadata.verification_identity`. Prefer an existing identity adapter:
`harness` binds candidate file hashes; `content` binds CAS candidate identity. Neither stdout
nor an Agent's report is a trusted score. Keep benchmark-specific execution in its adapter,
not the generic coordinator. A new benchmark does not automatically need a built-in catalog entry.

For the legacy fixed workflow, follow the Bundle instructions below. Prefer caller-owned
manifests with `create_registry` for external integrations; register in the built-in catalog
only when the requested product surface requires it.

## Inspect The Legacy Contract

Read these files before editing:

- `src/areal_rsi/benchmarks/config/supported_benchmarks.yaml`
- `src/areal_rsi/benchmarks/catalog.py`
- `src/areal_rsi/benchmarks/adapters.py`
- `src/areal_rsi/api/modules.py`
- The closest implementation under `src/areal_rsi/modules/` and its tests

Determine the upstream data identity, public task format, supported splits, hidden verification
data, result format, aggregate metrics, configuration parameters, and whether execution is local
or remote. Do not assume that the location of the catalog and the execution provider are the same.

## Implement a Legacy Bundle

1. Implement or reuse a `Benchmark` that returns public `Task` objects for `train` and `validate`.
2. Implement or reuse its matching `Verifier`. Keep expected answers and private validation data
   inside the host-side verifier; never include them in `Task`, Agent workspaces, descriptions, or
   trajectory bundles supplied to research Agents.
3. Reuse an existing `Evaluation` when its aggregation semantics match. Add a new one only when the
   Benchmark requires different metrics.
4. Add a thin trusted Adapter that validates the declared verifier/evaluation pairing and returns a
   `BenchmarkBundle`.
5. Register the Adapter under a stable name in the chosen caller-owned or built-in manifest.
   Do not add a Benchmark-specific branch to `LocalApp`.

The Manifest may describe locations, public metadata, parameter schemas, verifier/evaluation IDs,
and provider names. It must not contain credentials, expected answers, shell commands, or arbitrary
Python import paths. Reference credentials by environment-variable name when a provider needs them.

Keep the stable catalog ID separate from the immutable runtime instance ID. The instance ID must
change when data identity, split membership, or effective sampling changes, so stopped runs cannot
resume against different tasks.

For public datasets, pin the upstream revision and verify downloaded bytes when possible. Build
research `validate` from data intended for development and do not repeatedly select versions on an
official test split. Document what the reported score does and does not represent.

## Stateful Tasks and Upstream Execution

Inspect actual execution code as well as the README. Identify the sampling/execution unit,
runtime and model/judge interfaces, resource limits, grading formula and persistence backend.
Container runtime and tool sandbox are separate choices; a flag's existence does not establish
support for every persistence mode.

For sequences, retain episode order, fresh-session boundaries, snapshot/restore/reset rules,
reflection steps and paired controls. Split and schedule at family level when episodes depend
on one another. Do not flatten them into independent tasks or equate learning episodes with
model-weight training. Prefer the official runner and grader behind a thin wrapper. Make any
necessary upstream patch explicit, versioned and tested; explain its effect on comparability.

Bind results to the candidate, effective task membership, protocol, target/judge models,
budgets, dependencies and immutable image identity. Store credentials by environment-variable
reference, never in candidates or command-line arguments. Inspect upstream traces before
publication: they may contain credentials as well as hidden tasks. Export development evidence
through an explicit allowlist and keep validation artifacts private. Separate directories are
not a security sandbox; document the actual isolation boundary.

Use disjoint development/search/final pools at the correct grouping level. Label local research
splits and subsets accurately, not as official full-suite results. A deliberately shared
diagnostic case is not evidence of generalization.

Use fresh output/state directories, bounded execution, scoped cancellation/cleanup and port
coordination. Never clean up unrelated containers. Reject missing/duplicate/unexpected episodes,
wrong identities, malformed/non-finite scores and incomplete runs. Model task failure is a valid
observation; API/service/evaluator failure is not a zero-quality candidate. Preserve provenance
for upstream proxies and defaults, and define family/task aggregation weights explicitly.

## Verify The Adapter

Add offline tests using a small synthetic fixture. Cover the applicable interfaces:

- Manifest loading, parameter defaults/types, and unknown configuration rejection.
- Non-empty, unique tasks and disjoint `train`/`validate` identities.
- Absence of expected answers and private paths from public tasks.
- Correct, incorrect, malformed, and tampered result verification.
- Deterministic instance identity and identity changes when effective tasks change.
- Bundle construction with the declared Benchmark, Verifier, and Evaluation.
- Research result binding through the actual trusted result reader.
- Stateful sequence/control preservation, incomplete runs and candidate edit boundaries.

When a maintained real dataset is locally available, instantiate both the direct implementation and
the registered Adapter with identical configuration, then compare instance IDs and serialized tasks.
Do not download large data or start paid model runs unless the user requested that external action.
An already-requested smoke run needs no extra approval. Distinguish preflight, synthetic tests,
real model execution, task success and demonstrated improvement in the handoff; do not expand a
smoke test into an unrequested full-suite sweep.

Run the focused tests first, then the repository's full test, lint, and type checks. Add comments only
where they explain an isolation, trust, or reproducibility invariant that is not evident from code.
