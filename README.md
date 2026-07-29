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
