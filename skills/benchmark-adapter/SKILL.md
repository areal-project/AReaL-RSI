---
name: benchmark-adapter
description: Adapt a local or remote Benchmark to AReaL-RSI through its SupportBenchmark manifest, trusted adapter registry, task/verifier contract, and reproducibility checks. Use when adding a Benchmark, dataset, verifier, evaluation, or provider integration to this repository.
---

# Adapt A Benchmark

Integrate the requested Benchmark without coupling it to an RSI optimization target. A Benchmark
defines what is evaluated; `ResearchConfig.evolution_target` independently determines whether an experiment
changes a Harness, Model, or Artifact.

## Inspect The Existing Contract

Read these files before editing:

- `src/areal_rsi/infra/tasks/config/supported_benchmarks.yaml`
- `src/areal_rsi/infra/tasks/catalog.py`
- `src/areal_rsi/api/interfaces.py`
- The closest implementation under `src/areal_rsi/infra/tasks/` and its tests

Determine the upstream data identity, public task format, supported splits, hidden verification
data, result format, aggregate metrics, configuration parameters, and whether execution is local
or remote. Do not assume that the location of the catalog and the execution provider are the same.

## Implement The Integration

1. Implement or reuse a `Benchmark` that returns public `Task` objects for `train` and `validate`.
2. Implement or reuse its matching `Verifier`. Keep expected answers and private validation data
   inside the host-side verifier; never include them in `Task`, Agent workspaces, descriptions, or
   trajectory bundles supplied to research Agents.
3. Reuse an existing `Evaluation` when its aggregation semantics match. Add a new one only when the
   Benchmark requires different metrics.
4. Add a thin trusted Adapter that validates the declared verifier/evaluation pairing and returns a
   `BenchmarkBundle`.
5. Register the Adapter under a stable name and add a declarative entry to
   `config/supported_benchmarks.yaml`. Do not add a Benchmark-specific branch to `ResearchSystem`.

The Manifest may describe locations, public metadata, parameter schemas, verifier/evaluation IDs,
and provider names. It must not contain credentials, expected answers, shell commands, or arbitrary
Python import paths. Reference credentials by environment-variable name when a provider needs them.

Keep the stable catalog ID separate from the immutable runtime instance ID. The instance ID must
change when data identity, split membership, or effective sampling changes, so stopped runs cannot
resume against different tasks.

For public datasets, pin the upstream revision and verify downloaded bytes when possible. Build
research `validate` from data intended for development and do not repeatedly select versions on an
official test split. Document what the reported score does and does not represent.

## Verify The Adapter

Add offline tests using a small synthetic fixture. Cover:

- Manifest loading, parameter defaults/types, and unknown configuration rejection.
- Non-empty, unique tasks and disjoint `train`/`validate` identities.
- Absence of expected answers and private paths from public tasks.
- Correct, incorrect, malformed, and tampered result verification.
- Deterministic instance identity and identity changes when effective tasks change.
- Bundle construction with the declared Benchmark, Verifier, and Evaluation.

When a maintained real dataset is locally available, instantiate both the direct implementation and
the registered Adapter with identical configuration, then compare instance IDs and serialized tasks.
Do not download large data or start paid model runs unless the user requested that external action.

Run the focused tests first, then the repository's full test, lint, and type checks. Add comments only
where they explain an isolation, trust, or reproducibility invariant that is not evident from code.
