# RSI Problem Integration and Session API

The system entry point is `infra.session.SessionSystem`. Without a bound problem, `/rsi` continues to use the existing
Interaction → Leader flow. Problem binding adds candidate and evaluation capabilities without changing ownership of native history.
`SessionSystem` provides public session operations; `SessionRuntime` owns connections, runtime state, and the execution loop.
`AppConfig` defines startup defaults, while each session persists its own `SessionConfig`.

## Launching a Problem

The control node needs an existing RSI Python environment, Codex, a reachable research model API, and a Slurm client.
TIR/Search GPU dependencies live in their respective Apptainer runtime environments. Credentials are read from a private env file,
not stored in problem configuration or recipes. Run the following examples from a source checkout with this repository installed.

Run from the repository root and retain this `PYTHONPATH` when launching, resuming, and running final evaluation:

```bash
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
areal-rsi research \
  --problem examples.tir.problem:TIRProblem \
  --config /absolute/tir-experiment.json \
  --objective /absolute/objective.md \
  --root /absolute/runs/tir-001 \
  --env-file /absolute/private.env
```

TIR configuration follows the structure in `examples/tir/experiment.json` and requires actual AReaL source code, a base model,
a GPU Python environment, a fixed evaluation benchmark, and an available image. `image`, `binds`, and `slurm` are top-level configuration fields. `binds` explicitly specifies container mounts at identical host/container paths;
when `slurm.reservation` is `null`, no reservation is requested. Partition selection follows the cluster's default scheduling policy.
The benchmark must have separate valid/test splits; an initial training pool is optional. Without a training set, first create a benchmark
from evaluation JSONL in `Example` format; datasets/pyarrow are not required:

```bash
python -m examples.tir.benchmark \
  --valid /absolute/valid.jsonl --test /absolute/test.jsonl \
  --output /absolute/benchmark/tir-001
```

Optionally provide official training questions as JSONL through `--train`; omitting it does not create an empty training file.
Startup freezes only the evaluation protocol and baseline, without generating a training set. In its first round, Leader submits a recipe with `real_count=0`
and `synthetic_count>0`; each round uses `build_candidate` to generate and freeze its own training data.
A training pool is required only when real questions are requested. For synthetic-only recipes and the JSONL format, see the section on
starting without an initial training set in [the TIR research guide](../../examples/tir/RESEARCH.md).

The entry point for preparing raw ToRL parquet remains `python -m examples.tir.benchmark --source ... --output ...`;
run it in an environment with datasets/pyarrow, then pass the prepared benchmark to the research entry point.

Search uses `examples.search.problem:SearchProblem`; see `examples/search/research.json` for the full template. The configuration structure is:

```json
{
  "search": {"...": "Fields of SearchConfig"},
  "image": "/absolute/training.sif",
  "slurm": {"reservation": null, "cpus": 32, "memory_gb": 256},
  "min_rounds": 1
}
```

Populate the `search` object according to `examples/search/experiment/config.py:SearchConfig`,
including the model, frozen source project, AReaL environment, data/index, embedding, and retrieval Python environment.
The illustrative object above is not a complete runnable configuration. A Search recipe is `{"training": {...}}`,
with fields defined by `TrainingConfig` in the same file.

Startup first prints `run_id`, then prepares the frozen protocol and runs the valid baseline before handing off to the actual Leader.
The original objective body is saved. Resuming reuses the binding, configuration, and goal; replacement configuration cannot be supplied at the same time.
Recovery first connects to the existing session and preserves any pending phase handoff, avoiding duplicate entry into RSI:

```bash
areal-rsi research --root /absolute/runs/tir-001 --resume run-XXXXXXXXXXXX --env-file /absolute/private.env
areal-rsi status --root /absolute/runs/tir-001 --resume run-XXXXXXXXXXXX
areal-rsi finalize --root /absolute/runs/tir-001 --resume run-XXXXXXXXXXXX --candidate r2
```

status does not start a model; finalize does not start a research Agent and runs an independent test only for an already frozen candidate.
TIR retains repeated samples, and final results include paired statistics clustered by question. Search's default test uses held-out external data.
With `official: FinalEvalConfig` and `official_image` provided, finalize uses fixed H0,
three GPUs, the official 830 questions, and the judge, retaining raw scoring artifacts and checkpoint compatibility export evidence.
The final `target_met` expresses only the primary metric improvement target in the configuration, not all acceptance criteria in the objective.

## Implementing a Problem

Problem classes are loaded through an operator-specified `module:Class`; infra does not hardcode example names.
The class declares `config_type` and `recipe_type`, accepts a decoded configuration dataclass in its constructor, and implements:

| Interface | Problem responsibility |
| --- | --- |
| `prepare(root) -> ProblemSpec` | Prepare local resources; declare the baseline, fixed comparison conditions, locked files, and source scope |
| `build_candidate(context, recipe) -> CandidatePlan` | Prepare candidate data/artifacts; return parent and child solutions and an optional training job |
| `build_evaluation(context) -> EvaluationPlan` | Return evaluation jobs, expected sample IDs, input references, and independent question groups |

Preparation and plan construction may write local files; remote jobs must be executed by infra. Problems do not need to implement session driving,
the candidate state machine, Slurm submission receipts, result caching, or selection freezing. See the implementations of all three interfaces in
`examples/tir/problem.py` and `examples/search/problem.py`.

Training data may be generated during candidate preparation in each round and need not be a startup input. Fixed evaluation conditions and evolvable training data
are recorded separately; each round saves the recipe, generator source snapshot, data content references, and parent model. Resuming an existing candidate plan
reuses and validates saved data without synthesizing it again. Failed or interrupted preparation retains its records; a new recipe uses a new candidate ID.
Current TIR synthesis uses a local program; independent GPU or teacher-model generation jobs still need integration through infra execution plans.

A `CandidatePlan` without a training job uses the frozen solution directly, supporting harness/artifact evolution.
With a training job, it provides an AReaL runtime and JobSpec. The worker publishes a `TrainingReceipt` only after actual training and checkpoint writing finish;
infra validates step counts, weight shards, and content digests. The evaluation worker publishes a complete
`samples.jsonl`, with one `EvaluationSample` per line. Task errors are scored; execution failures fail the job.

Comparison conditions and the objects allowed to evolve are stored separately. Model research fixes the harness; harness research records harness changes as
solution content while keeping external data, scoring, and budget agreements consistent. The primary metric and maximize/minimize direction
are declared by `ComparisonContract`. Custom problem configuration must not enter internal state as an undecoded dictionary.

## Execution and Recovery

Each run's problem records live in `sessions/<run_id>/problem/`: configuration, protocol/freeze, candidates,
source archives, execution receipts, trajectories, selection, final.json, and report.md. Session lifecycle remains managed solely by
SessionRecord, process lifecycle by JobRecord, and candidate state by CandidateRecord.

Candidate submission saves the request and returns a handle first; local data preparation, training, and validation run in the background. Preparation failures also retain queryable records,
and incomplete preparation steps are not automatically replayed. After the Leader turn ends, the host waits for jobs to complete before
continuing automatically, avoiding idle model calls. Use the candidate query tool to retrieve feedback and valid evidence. After a failure or controller restart,
explicitly invoke the evaluation tool to resume a candidate; completed training/evaluation reuses validated artifacts. Retrying a failed valid phase requires
explicit `retry=true`, creates a new attempt, and preserves old records.
After a recovery request for an existing plan is accepted, the tool returns a pending execution state and clears the old error; subsequent failures are reported through background feedback.

Slurm uses held submission: the remote ID and initial receipt are saved before release. If the response is lost but a receipt exists,
reconnect to the same job. If the submission outcome is unknown, retain the submission directory and refuse automatic resubmission; check the job name in the record.
Normal controller exit or disconnection leaves submitted Slurm jobs running; pausing a session does not cancel external jobs either.
Explicit stop cancels only jobs recorded in that run's receipts. Cancellation requests and confirmed resource release are recorded separately.
Local process interruption cleans up its own process tree; recovery does not blindly replay it.
Software timeouts for unfinished jobs are measured from the first submission request, including queue time and controller disconnection; reconnecting does not reset the budget.
Successfully completed jobs can still reuse validated artifacts.
Cancellation and timeout retain their respective terminal states; resources are recorded as released only after confirming a Slurm terminal state.
If a completed job has been purged from scontrol, recovery can confirm its terminal state through sacct using the same job ID, without resubmitting.

`SlurmResources.node` can specify a node to avoid rerunning experiments on nodes with unexpected existing GPU usage.
GPU containers share the `infra/execution/worker.sh` entry point. Before loading a model, it saves `gpu-allocation.json`, checks allocated
devices against CUDA-visible devices, and rejects GPUs with more than 1 GiB of existing memory usage. On failure, it retains device and memory
records and exits with a failed job; it does not work around occupancy by shrinking training microbatches or dropping samples.

Changes to fixed evaluation source code, configuration, or inputs prevent continuation. Editable problem source code may change between rounds;
each candidate records its corresponding snapshot, and source consistency is checked before and after execution stages. Historical experiments use their original frozen runtimes,
without rewriting old protocols, source-lock records, candidate digests, or artifacts.
Source snapshots always exclude Python bytecode caches, regardless of whether the run directory contains a `.gitignore`; shared workers disable bytecode writes.

## User Methods and Agent Tools

Existing methods remain: create/open/detail, send_message, enter_rsi/start, pause/resume/stop/chat,
answer, events, set_budget, learn, and others. `create(config, problem=...)` optionally binds a problem.
All new methods take run_id: prepare_problem, problem_detail, submit_candidate, candidate_status,
evaluate_candidate, select_candidate, and finalize_problem.

Only bound problems register rsi_submit_candidate, rsi_candidate_status, rsi_evaluate_candidate,
and rsi_select_candidate. Tools validate the Leader identity against the current thread/turn; JSON requests reuse api/control.py.
Selection requires complete, comparable valid evidence, satisfaction of minimum-round and improvement requirements, and the best candidate among those completed.
The completion tool revalidates the frozen selection; textual evidence cannot replace actual results. Final test evaluation is not exposed as an Agent tool.

API data lives in api/records.py, research.py, and execution.py; protocols in interfaces.py; and control requests in
control.py. infra does not statically import examples or UI. Embedded UI calls SessionSystem; MCP and JSON entry points
call the same system through the background service.
Other retained protocols do not imply existing implementations or provide a second problem integration path.

## External Harnesses

Public tool arguments and schemas share `api/control.py:OPERATIONS`. See the [plugin protocol](../plugins.md#请求与能力)
for the operation list and retry semantics. External controls cannot invoke the internal Agent's rsi_update_goal
or bypass Leader role checks to submit or select candidates directly. The host can query candidates and run independent final acceptance.
