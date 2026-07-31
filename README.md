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

1. Submit an EnvArena evaluation for the current model path and collect trajectories.
2. Generate a dataset from trajectories.
3. Run AReaL SFT training.
4. Run holdout evaluation for the new checkpoint.
5. Record the run in Memory.
6. Let RSIAgent explain the run and propose the next action.

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

The adapter's platform-neutral contract is the evaluation lifecycle:
`submit_eval`, `get_eval_progress`, `get_eval_result`, and `get_trajs`.
Set the control-plane address with `control_base_url` or, preferably for
deployment-specific configuration, `control_base_url_env` (default:
`ENVARENA_CONTROL_BASE_URL`). The value may be either the service root or end
in `/openapi/v1`. `envarena.job_config` can define defaults such as
`sample_numbers`, `concurrency_policy`, `tags`, `model_parameters`, and
`model_env`; values in each `EvalRequest.job_config` override those defaults.

Model, benchmark, harness, and skill are evaluation inputs, not global client
defaults. `model` and `skill` are asset configs with `type: "ref"` for an
existing Arena asset or `type: "new"` when the client should create/register or
start it from the remaining fields. `benchmark` and `harness` are existing
Arena refs only. `benchmark` is the old task set concept.

```python
from areal_rsi.schemas import EvalRequest

job = client.submit_eval(EvalRequest(
    model={"type": "ref", "registry_ref": {"key": "your-model", "version": "1.0.0"}},
    benchmark="your-benchmark@1.0.0",
    harness={"key": "your-harness", "version": "1.0.0"},
    skill={"type": "ref", "ref": "your-skill@1.0.0"},
    job_config={"sample_numbers": 3, "tags": ["rsi"]},
))
```

After updating the OpenAPI file, regenerate the vendored SDK with the configured
Alibaba Cloud PyPI mirror:

```bash
bash scripts/generate_envarena_client.sh
```

## AReaL SFT Service

`AReaLService` delegates SFT execution to `AReaLSFTWrapper`, so callers do not
need to use the wrapper directly. The supplied AReaL YAML controls model, data,
engine, resources, artifact root, and checkpoint policy. The service config
only selects a scheduler (`local` or `slurm`), environment variables, optional
Slurm settings, and the command that starts the training YAML.

```python
from areal_rsi.services import AReaLService

areal = AReaLService.from_yaml("/path/to/areal.yaml")

train_config = {"train_config_path": "/path/to/gsm8k-sft.yaml"}
run = areal.train("/models/base", {"examples": []}, "sft", train_config)
artifacts = areal.get_experiment_artifacts(train_config)
```

The wrapper invokes the configured command followed by `--config ...`, returns
the latest AReaL checkpoint for a local run, and captures output under the
trial log directory. Choose a different driver or entrypoint by changing the
configured command (or the per-request `command`), not by adding a driver field
to the wrapper.

For the tested containerized Slurm/SPMD route, configure the optional
`areal.sft.slurm` section in that same service YAML.
`start_experiment()` performs both script generation and `sbatch --parsable`
submission when `scheduler_type` is `slurm`. Submission is asynchronous and
therefore returns a job record, not a completed `TrainRun` checkpoint.

```python
from areal_rsi.services import AReaLService

areal = AReaLService.from_yaml("/path/to/areal.yaml")

job = areal.start_experiment(
    {
        "train_config_path": "/path/to/gsm8k-sft.yaml",
        "scheduler_type": "slurm",
    }
)
print(job["job_id"], job["script_path"])
```

The generated job launches one `torchrun` process per allocated node. The
experiment YAML provides `cluster.n_nodes`, `cluster.n_gpus_per_node`, and `cluster.fileroot`.
The wrapper creates per-run cache and Slurm-log directories below that root.
Do not put credentials in `container_environment`; inject them at job runtime.
