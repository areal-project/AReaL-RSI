# AReaL-RSI

AReaL-RSI is an automated research and training loop for recursive model improvement built on top of AReaL and EnvArena.

It turns environment feedback into reusable training signal:

```text
EnvArena Traj
  -> ResearchAgent synthesizes Experience
  -> Dataset construction
  -> AReaL SFT / OPD training
  -> EnvArena evaluation
  -> Memory records evidence and decisions
  -> next ckpt
```

The system keeps two roles separate:

- `RSIAgent`: conversational research interface. It explains state, proposes plans, incorporates human feedback, and emits structured actions.
- `ResearchAgent`: trajectory-to-dataset worker agent. It analyzes EnvArena trajectories, synthesizes experience, and emits datasets.
- `RSIController`: deterministic workflow executor. It runs jobs, persists state, supports resume/retry/stop, and records artifacts.

Concrete agent scaffolds are backed by OpenAI SDK through the optional
`openai` dependency. Mock services remain available for local
controller tests and demos.

## Documents

- [Design Doc](docs/design.md)
- [Interface Contracts](docs/interfaces.md)
- [Development Plan](docs/development_plan.md)
- [Module Breakdown](docs/module_breakdown.md)

## MVP Goal

Build a minimal but recoverable loop:

1. Register an AReaL inference endpoint in EnvArena.
2. Submit an EnvArena evaluation and collect trajectories.
3. Generate a dataset from trajectories.
4. Run AReaL SFT training.
5. Run holdout evaluation for the new checkpoint.
6. Record the run in Memory.
7. Let RSIAgent explain the run and propose the next action.

## Local Mock Loop

```bash
python -m areal_rsi.cli.main run-mock --ckpt ckpt_000 --task-set swe_verified
```

Run multiple mock RSI iterations under one `run_id`:

```bash
python -m areal_rsi.cli.main run-loop-mock --ckpt ckpt_000 --task-set swe_verified --max-iterations 2
```

Run from an RSI config file:

```bash
python -m areal_rsi.cli.main run --config config/rsi.example.yaml
```

Chat can generate a structured action without executing it:

```bash
python -m areal_rsi.cli.main chat --backend fake "run ckpt_000 on swe_verified"
```

If no message is provided, `chat` starts an interactive REPL:

```bash
python -m areal_rsi.cli.main chat --backend openai --config config/rsi.example.yaml --execute --yes
```

Use `--stdin` when you want a single non-interactive message from stdin.

To execute a confirmed action in the local mock loop:

```bash
python -m areal_rsi.cli.main chat --backend fake --execute --yes "run ckpt_000 on swe_verified"
```

Chat can also load a base config and let the user message override only the
fields it mentions:

```bash
python -m areal_rsi.cli.main chat --backend openai --config config/rsi.example.yaml --execute --yes \
  "use ckpt_123 and run swe_verified for 2 iterations with sft"
```

`chat` emits user-facing commands such as `start_loop`, `inspect_run`,
`stop_run`, and `resume_run`. Internal workflow steps like
`eval`, `train`, and `holdout_eval` are recorded in `audit.jsonl` rather
than exposed as chat actions.

For scripted state queries, use the CLI status command:

```bash
python -m areal_rsi.cli.main status <run_id> --run-root runs
```

OpenAI SDK support is optional:

```bash
pip install -e '.[agent]'
```

## EnvArena Control Plane Client

The real EnvArena adapter is generated from
`docs/envarena-openapi.yaml`, then wrapped by `EnvArenaClient` so the
controller does not depend on generated implementation details. Copy and
adjust the YAML example, then provide the bearer token through its configured
environment variable:

The platform-neutral contract and local mock live in
`areal_rsi.services.env_client`; EnvArena's implementation and generated SDK
live in `areal_rsi.services.env_client.envarena`.

```bash
cp config/envarena.example.yaml config/envarena.yaml
export ENVARENA_API_TOKEN='...'
```

```python
from areal_rsi.services import EnvArenaClient

client = EnvArenaClient.from_yaml("config/envarena.yaml")
benchmarks = client.list_benchmarks()
skills = client.list_skills()
```

The adapter supports gateway-model upsert, benchmark/skill/harness discovery,
BenchmarkJob submission, job summary/result lookup, and trajectory retrieval.
Set the control-plane address with `control_base_url` or, preferably for
deployment-specific configuration, `control_base_url_env` (default:
`ENVARENA_CONTROL_BASE_URL`). The value may be either the service root or end
in `/openapi/v1`.

Harness and Benchmark versions are evaluation inputs, not global client
defaults. Construct an `EvalRequest` with `harness_config["harness_ref"]` and
a `"benchmark@version"` task set, then pass it to `submit_eval`.

```python
from areal_rsi.schemas import EvalRequest

job = client.submit_eval(EvalRequest(
    model_ref={"registry_ref": {"key": "your-model", "version": "1.0.0"}},
    task_set="your-benchmark@1.0.0",
    harness_config={"harness_ref": {"key": "your-harness", "version": "1.0.0"}},
))
```

After updating the OpenAPI file, regenerate the vendored SDK with the configured
Alibaba Cloud PyPI mirror:

```bash
bash scripts/generate_envarena_client.sh
```

## AReaL SFT Service

`AReaLService` delegates SFT execution to `AReaLSFTWrapper`, so callers do not
need to use the wrapper directly. The supplied AReaL YAML controls the model,
datasets, scheduler (`local`, `ray`, or `slurm`), engine, resources, artifact
root, and checkpoint policy. Configure only the execution environment in
Python, then put the experiment YAML path in the existing `train_config`:

```python
from areal_rsi.services import AReaLService, AReaLSFTConfig

areal = AReaLService.from_sft_config(AReaLSFTConfig(
    command=("areal",),
    working_directory="/path/to/AReaL",
))

train_config = {"experiment_config": "/path/to/gsm8k-sft.yaml"}
run = areal.train("/models/base", {"examples": []}, "sft", train_config)
artifacts = areal.get_experiment_artifacts(train_config)
```

The wrapper invokes `areal train run --config ... --driver ...`, returns the
latest AReaL checkpoint, and captures output under the trial log directory.
The driver defaults to `examples.math.gsm8k_sft:main`; set `"driver"` in
`train_config` only when a particular experiment needs an override.

For the tested containerized Slurm/SPMD route, configure `AReaLSlurmConfig`.
`render_sbatch_script()` lets a caller inspect the script; `write_sbatch_script()`
persists it; `submit_experiment()` performs both persistence and `sbatch --parsable`
submission. Submission is asynchronous and therefore returns a job record, not
a completed `TrainRun` checkpoint.

```python
from areal_rsi.services import AReaLService, AReaLSFTConfig, AReaLSlurmConfig

areal = AReaLService.from_sft_config(AReaLSFTConfig(
    working_directory="/storage/openpsi/users/xuruida.xrd/AReaL",
    slurm=AReaLSlurmConfig(
        image="/storage/openpsi/images/areal-dev-20260508.sif",
        offline_datasets=True,
    ),
))

job = areal.submit_experiment({"experiment_config": "/path/to/gsm8k-sft.yaml"})
print(job["job_id"], job["script_path"])
```

The generated job launches one `torchrun` process per allocated node and
requires `scheduler.type: null`, which is the SPMD path. The experiment YAML
provides `cluster.n_nodes`, `cluster.n_gpus_per_node`, and `cluster.fileroot`.
The wrapper creates per-run cache and Slurm-log directories below that root.
Do not put credentials in `container_environment`; inject them at job runtime.
