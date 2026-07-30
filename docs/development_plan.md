# AReaL-RSI Development Plan

## 1. Product Goal

AReaL-RSI should provide an interactive, auditable, and recoverable research loop for improving models with EnvArena trajectories and AReaL training.

The first prototype should optimize for a working closed loop, not a fully general AutoResearch platform.

## 2. Core Principles

- `RSIAgent` converts user goals and queries into user-facing commands.
- `RSIController` decides how to execute it reliably.
- Natural language should produce structured actions, not directly replace workflow execution.
- EnvArena owns env, harness, task execution, trajectory storage, and evaluation progress.
- AReaL owns inference endpoint hosting and training.
- Memory must be machine-readable enough for recovery and human-readable enough for research iteration.

## 3. Milestones

### M0: Repository Foundation

Goal: make the project structure explicit before coding the loop.

Deliverables:

- README.
- Development plan.
- Module breakdown.
- Initial package layout proposal.
- Minimal interface definitions in docs.

Acceptance:

- A new contributor can understand the intended modules and first MVP path without reading external chat history.

### M1: Local Skeleton With Mock Services

Goal: build the loop without real AReaL or EnvArena dependencies.

Deliverables:

- Python package skeleton.
- `RSIController.run_iteration(...)`.
- Mock `AReaLService`, `EnvClient`, `ResearchAgent`, and `Memory`.
- OpenAI SDK backed `RSIAgent` and `ResearchAgent` scaffolds with lazy optional imports.
- Local run state persistence.
- Unit tests for successful run, resume, and failure recording.

Acceptance:

- One command runs a full mock iteration and produces a run directory with state, ledger, dataset, train result, and eval result.

Status: done for the current prototype. The local mock loop, OpenAI-backed chat
scaffold, and tests are committed.

### M2: EnvArena Read/Eval Integration

Goal: replace mock EnvArena with the real EnvArena API path.

Deliverables:

- EnvArena client adapter.
- Model endpoint registration/update/delete.
- Eval submission.
- Progress polling.
- Trajectory fetching.
- Basic trajectory normalization.

Acceptance:

- Given an existing AReaL-compatible endpoint, the controller can submit an EnvArena eval and fetch trajectories into Memory/artifacts.

Status: next.

### M3: AReaL Inference And SFT Integration

Goal: connect real AReaL inference hosting and SFT training.

Deliverables:

- AReaL rollout endpoint adapter.
- SFT training adapter.
- Checkpoint output tracking.
- Endpoint update after training.
- Holdout eval for the new checkpoint.

Acceptance:

- A full SFT iteration runs end to end with EnvArena trajectories and writes a new checkpoint reference plus holdout metrics.

### M4: RSIAgent Conversational Interface

Goal: add the human-facing research interface on top of the controller.

Deliverables:

- `RSIAgent.chat(...)`.
- Action proposal schema.
- Confirmation flow for expensive actions.
- Status/explanation queries.
- Human feedback recording in Memory.

Acceptance:

- A user can start a run, ask status, inspect failures, modify traj filtering, and accept/reject a checkpoint through conversational commands.

The first CLI slice can land before full M4:

```bash
python -m areal_rsi.cli.main chat --backend fake "run ckpt_000 on swe_verified"
python -m areal_rsi.cli.main chat --backend fake --execute --yes "run ckpt_000 on swe_verified"
python -m areal_rsi.cli.main chat --backend openai "run ckpt_000 on swe_verified"
```

### M5: OPD Path

Goal: support on-policy distillation as a second training path.

Deliverables:

- OPD action/config support.
- EnvArena rollout collection for OPD.
- Teacher/student trajectory metadata.
- OPD training adapter.
- OPD-specific Memory events.

Acceptance:

- The controller can run either SFT or OPD from the same structured action shape.

### M6: AutoResearch Evolution

Goal: generalize AReaL-RSI into one domain loop under a broader AutoResearch framework.

Deliverables:

- Task spec loader.
- Service registry abstraction.
- Workflow executor abstraction.
- Domain adapters for RSI, dataset search, reward search, and training recipe search.

Acceptance:

- AReaL-RSI remains the first domain loop, but the executor can run other research workflows using the same state, memory, and service contracts.

## 4. MVP Execution Flow

```text
User
  -> RSIAgent.chat(...)
  -> structured action: start_loop
  -> RSIController.run_loop(...)
  -> AReaL.ensure_rollout_endpoint(ckpt)
  -> EnvArena.register_model(endpoint)
  -> EnvArena.submit_eval(EvalRequest(...))
  -> EnvArena.get_trajs(eval_job)
  -> ResearchAgent.generate_dataset(trajs, memory_context, ckpt)
  -> AReaL.train(base_ckpt, dataset, method=sft)
  -> AReaL.update_rollout_endpoint(next_ckpt)
  -> EnvArena.submit_eval(EvalRequest(...))
  -> Memory.record(...)
  -> accept/stop and continue with next ckpt
  -> RSIAgent.explain(...)
```

## 5. Package Layout Proposal

```text
areal_rsi/
  agent/
    rsi_agent.py
    actions.py
  controller/
    rsi_controller.py
    run_state.py
    workflow.py
  services/
    env_client/
      base.py
      mock.py
    areal/
      service.py
      mock.py
    envarena/
      client.py
      generated/
    research/
      agent.py
      mock.py
      openai_agent.py
  memory/
    memory.py
    ledger.py
    artifacts.py
  schemas/
    core.py
    actions.py
  cli/
    main.py
tests/
  test_controller_mock.py
  test_memory.py
  test_agent_actions.py
docs/
  development_plan.md
  module_breakdown.md
```

## 6. First Implementation Slice

Build this first:

1. `schemas/core.py`: type aliases and minimal dataclasses.
2. `memory/ledger.py`: append/read JSONL events.
3. `controller/run_state.py`: save/load run state.
4. `services/{areal,envarena,research}/`: mock implementations.
5. `controller/local_controller.py`: mock end-to-end loop.
6. `agent/openai_agent.py`: OpenAI SDK backed agent scaffold.
7. Tests for the mock loop.

This gives a stable integration shell before binding to real AReaL and EnvArena.

## 7. Non-Goals For MVP

- No general task spec engine.
- No service registry.
- No independent Eval Agent.
- No complex database.
- No multi-user permission model.
- No automatic hidden benchmark diagnosis.
- No OPD implementation before the SFT path is stable.

## 8. Key Risks

- Long-running jobs can be duplicated if run state is not persisted before external submissions.
- Agent decisions can become untraceable if structured actions are not recorded.
- ResearchAgent can become a black box if traj filtering and experience synthesis are not logged.
- EnvArena trajectory schema may evolve; keep a normalization layer.
- AReaL training configs can become too broad; keep MVP training config minimal.

## 9. Immediate Next Tasks

1. Implement `EnvArenaCliClient` behind `EnvArenaClient`.
2. Add JSON-envelope subprocess wrapper for `arena --json`.
3. Map AReaL endpoint metadata to EnvArena `Model@v`.
4. Map `task_set` and `harness_config` to published Benchmark/Harness refs.
5. Submit and watch an EnvArena job.
6. Normalize EnvArena trajectories into local `Traj`.
7. Run one controller iteration with real EnvArena eval and mock training.
