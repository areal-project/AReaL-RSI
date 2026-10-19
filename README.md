<div align="center">

# AReaL-RSI

**Pursue persistent goals. Build reusable research experience.**

[Paper](https://arxiv.org/abs/2610.18083) · [Getting Started](#getting-started) · [User Guide](docs/usage.md) · [Research Examples](#research-examples) · [AReaL](https://github.com/areal-project/AReaL)

</div>

AReaL-RSI is an agent framework for sustained research and iterative improvement, built on
Codex's native execution runtime. Define a goal, let the agent execute and validate across
turns, and distill useful methods into experience for future work.

Our objective: **better results within the same time budget, or less time to reach the same result.**

## Highlights

- **Persistent execution:** goal clarification, context-preserving handoff, steering, pause/resume, and budget controls.
- **Reusable experience:** editable Markdown documents, automatic curation, and human review feedback.
- **Auditable research:** experiment records, evaluation checks, trajectories, and [AReaL](https://github.com/areal-project/AReaL) training integration.
- **Independent examples:** task-specific data, policies, and protocols stay outside the general-purpose core.

## Getting Started

Requires Python **3.11+**, [uv](https://docs.astral.sh/uv/), Node.js **22+**, npm, `patch`,
and Codex CLI **0.162.1+**. Ordinary agent work does not require a local GPU.

```bash
git clone https://github.com/areal-project/AReaL-RSI.git
cd AReaL-RSI
npm install -g @openai/codex@0.162.1
uv tool install --editable .
cp -n .env.example .env
```

Set your model connection in `.env`:

```dotenv
RSI_BASE_URL=https://api.openai.com/v1
RSI_API_KEY=your-api-key
RSI_MODEL=your-model-name
```

The provider must support the Responses API and tool calling. Then launch:

```bash
areal-rsi
# Or work in another project:
areal-rsi --workspace /absolute/path/to/project
```

The first launch needs network access for TUI dependencies. If the command is not found,
run `uv tool update-shell` and reopen your terminal. Configuration loads from the launch
directory's `.env`; use `--env-file` to select another file.

Default permissions are `danger-full-access` / `never`. Adjust `RSI_SANDBOX` and
`RSI_APPROVAL_POLICY` in `.env` to restrict execution.

Start a goal in the terminal:

```text
/rsi Improve data loading performance. Measure a baseline, preserve outputs,
and report tests and timing comparisons.
```

Discuss the constraints, then ask the agent to begin or use `/start` once the task is saved.
Use `/pause` and `/resume` to control execution, `/budget` to manage limits, and `/learn` to
curate experience. See the [user guide](docs/usage.md) for all commands and recovery behavior.

## Research Examples

TIR and Search implement the same ResearchProblem contract and use the unified research command.

| Example | Description | Resources |
| --- | --- | --- |
| Search research | Model training with fixed retrieval and independently judged evaluations | [Setup](examples/search/README.md) |
| TIR research | Agent-driven, multi-round research on data and training recipes; select on valid, then evaluate on test | [Setup](examples/tir/RESEARCH.md) · [Results](examples/tir/RESULTS.md) |

For multi-round TIR, configure `examples/tir/experiment.json` and `.env` on a Slurm cluster
with Apptainer and an AReaL training environment, then run from the repository root:

```bash
uv sync --locked
source .venv/bin/activate
areal-rsi research --problem examples.tir.problem:TIRProblem \
  --config /absolute/tir-experiment.json --objective /absolute/objective.md \
  --root /absolute/runs/tir-001 --env-file /absolute/private.env
```

## Documentation and Development

Detailed guides are currently in Chinese:

[Usage](docs/usage.md) · [Architecture](docs/architecture.md) ·
[API](docs/interfaces/README.md) · [Code walkthrough](docs/walkthrough.md) ·
[Testing](tests/README.md) · [Experience study](docs/experience-study.md)

Dependencies flow **UI / examples → infra → api**. The core does not import examples.
Read [AGENTS.md](AGENTS.md) before contributing, and run:

```bash
uv sync --locked
make check
```

Checks cover Python, native TUI behavior, Ruff, and Mypy; real API calls and GPU training
are separate validations.

## Citation

If AReaL-RSI's research ideas are useful to your work, please cite:

```bibtex
@article{zhou2026arealrsi,
  title   = {{AReaL-RSI}: Adaptive Scaling and Coordination of Recursive Self-Improvement},
  author  = {Zhou, Wenhao and Song, Jiajun and Wang, Haitao and Qiu, Zipeng and
             Peng, Yingqi and Xu, Ruida and Li, Jiale and Li, Wenshuang and
             Xu, Shusheng and Dong, Wei and Dai, Hao and Yang, Tongkai and
             Gao, Yi and Yuan, Binhang},
  journal = {arXiv preprint arXiv:2610.18083},
  year    = {2026},
  url     = {https://arxiv.org/abs/2610.18083}
}
```

We thank [AReaL](https://github.com/areal-project/AReaL) for its training infrastructure,
[Codex](https://github.com/openai/codex) for its execution runtime, and
[DSH](docs/tui-comparison.md) for the terminal components.
