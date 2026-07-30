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

Chat can generate a structured action without executing it:

```bash
python -m areal_rsi.cli.main chat --backend fake "run ckpt_000 on swe_verified"
```

To execute a confirmed action in the local mock loop:

```bash
python -m areal_rsi.cli.main chat --backend fake --execute --yes "run ckpt_000 on swe_verified"
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
