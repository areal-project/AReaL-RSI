# AReaL-RSI

AReaL-RSI is an automated research and training loop for recursive model improvement built on top of AReaL and EnvArena.

It turns environment feedback into reusable training signal:

```text
holdout eval current ckpt -> stop immediately if target is reached
  -> training-benchmark eval without skill
  -> ResearchAgent writes one Experience per task
  -> EnvArena appends Experiences as a task-scoped Skill and evaluates again
  -> ResearchAgent pairs original prompts with skill-assisted completions
  -> SFT without Experience in the prompt
  -> next ckpt (evaluated at the start of the next iteration)
```

The system keeps two roles separate:

- `RSIAgent`: conversational research interface. It explains state, proposes plans, incorporates human feedback, and emits structured actions.
- `ResearchAgent`: trajectory-to-dataset worker agent. It analyzes EnvArena trajectories, synthesizes experience, and emits datasets.
- `RSIController`: deterministic workflow executor. It runs jobs, persists state, supports resume/retry/stop, and records artifacts.

The conversational RSIAgent has an OpenAI SDK scaffold. The worker ResearchAgent
also has a Claude Agent SDK implementation that uses one resumable Claude Code
session per iteration, reads one task trajectory file at a time, and writes the
iteration SFT JSONL and complete AReaL training YAML. Mock services remain
available for deterministic controller tests and demos.

One user command starts a **Run**. Each pre-training holdout → experience
distillation → train round inside that Run is one **iteration**. The config key
`run.max_iterations` limits the number of iterations in that Run.

## Documents

- [Design Doc](docs/design.md)
- [Interface Contracts](docs/interfaces.md)
- [Development Plan](docs/development_plan.md)
- [Module Breakdown](docs/module_breakdown.md)

## MVP Goal

Build a minimal but recoverable loop:

1. Evaluate the current checkpoint on the holdout benchmark and stop before training when the target is reached.
2. Evaluate the training benchmark without a skill and generate one Experience per task.
3. Submit Experiences as an Arena Skill and collect skill-assisted training trajectories.
4. Build SFT data from original prompts plus skill-assisted completions, with Experience removed from prompts.
5. Run AReaL SFT training and evaluate its checkpoint at the next iteration's start.
6. Record each iteration in Run-scoped Memory and let RSIAgent explain the Run.

## Local Mock Loop

```bash
python -m areal_rsi.cli.main run-mock --ckpt ckpt_000 --task-set swe_verified
```

Run one mock Run containing two iterations:

```bash
python -m areal_rsi.cli.main run-loop-mock --ckpt ckpt_000 --task-set swe_verified --max-iterations 2
```

Run from an RSI config file:

```bash
python -m areal_rsi.cli.main run --config config/rsi.yaml
```

Chat can generate a structured action without executing it:

```bash
python -m areal_rsi.cli.main chat --backend fake "run ckpt_000 on swe_verified"
```

If no message is provided, `chat` starts an interactive REPL:

```bash
python -m areal_rsi.cli.main chat --backend openai --config config/rsi.yaml --execute --yes
```

Use `--stdin` when you want a single non-interactive message from stdin.

To execute a confirmed action in the local mock loop:

```bash
python -m areal_rsi.cli.main chat --backend fake --execute --yes "run ckpt_000 on swe_verified"
```

Chat can also load a base config and let the user message override only the
fields it mentions:

```bash
python -m areal_rsi.cli.main chat --backend openai --config config/rsi.yaml --execute --yes \
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

Each Run is physically isolated under the run root:

```text
<run_root>/
  run_<timestamp>/
    state.json
    audit.jsonl
    resolved_rsi_config.json
    memory/
      ledger.jsonl
    artifacts/
      iter_001/
        research/
      iter_002/
        ...
```

Researcher receives only the current iteration's trajectories/workspace plus
the current Run's materialized `memory.json`. It is explicitly instructed not
to inspect sibling or previous Run directories. A new Researcher session is
created for every iteration; history crosses iterations only through that
Run's Memory.

OpenAI SDK support is optional:

```bash
pip install -e '.[agent]'
```

Claude ResearchAgent support is optional:

```bash
pip install -e '.[researcher]'
```

`config/rsi.yaml` currently combines real AReaL Slurm training and the
Claude ResearchAgent with `MockEnvClient`. Checkpoint-backed model startup in
EnvArena is intentionally mocked until its control-plane API is available. The
mock trajectories retain the same checkpoint path passed to the ResearchAgent.

## Agent Skill Launch

The repository includes one shared `start-rsi-experiment` Skill for Codex and
Claude Code. Invoke it as `$start-rsi-experiment` in Codex or
`/start-rsi-experiment` in Claude Code. The agent collects only the experiment
inputs that vary (experiment name, checkpoint, train and holdout benchmarks,
harness, iteration limit, and target reward), shows the complete resolved
summary, and waits for explicit confirmation before starting anything.

After confirmation, the Skill creates an isolated launch config under the
ignored `.rsi/launches/` directory and starts the Controller. Fixed deployment
settings stay in `config/rsi.yaml`, `config/areal.yaml`, and
`config/envarena.yaml`; the Skill does not ask for runs/experiments roots,
Arena URL, image, or Slurm settings unless the user asks to change them.

The launcher reads `ENVARENA_API_TOKEN` from the repository-local `.env` file.
The file is intentionally ignored by Git and the token is never copied into a
resolved config, receipt, log message, commit, or command line.

## EnvArena Control Plane Client

The real EnvArena adapter is generated from
`docs/envarena-openapi.yaml`, then wrapped by `EnvArenaClient` so the
controller does not depend on generated implementation details. Stable client
settings live in `config/envarena.yaml`; credentials stay in the ignored
repository-local `.env` file and are loaded automatically by the experiment
Skill launcher.

The platform-neutral contract and local mock live in
`areal_rsi.services.env_client`; EnvArena's implementation and generated SDK
live in `areal_rsi.services.env_client.envarena`.

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
The RSI controller polls the Slurm job and resolves the latest declared AReaL
checkpoint before completing the iteration. Its holdout evaluation happens at
the beginning of the next iteration.

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
