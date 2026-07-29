# AReaL-RSI Module Breakdown

## 1. Boundary Overview

```text
Human
  <-> RSIAgent
        -> RSIController
              -> AReaLService
              -> EnvArenaClient
              -> ResearchAgent
              -> Memory
              -> EvalFirewall
```

`RSIAgent` is the strategy and conversation layer. `RSIController` is the deterministic execution layer.

## 2. RSIAgent

Purpose:

- Convert natural language into structured actions.
- Explain run state, dataset choices, training choices, and checkpoint decisions.
- Incorporate human feedback.
- Ask for confirmation before expensive or irreversible operations.

Inputs:

- User message.
- Workflow spec.
- Current run state from `RSIController`.
- Memory context.
- Artifact summaries.

Outputs:

- Human-readable reply.
- Structured actions.
- Confirmation requirement.

Core interface:

```python
class RSIAgent:
    def chat(self, message: str, *, run_id: str | None = None) -> dict:
        ...

    def propose_next_action(self, *, run_state: dict, memory_context: dict) -> dict:
        ...

    def explain(self, *, run_id: str, question: str) -> str:
        ...
```

MVP behavior:

- Rule-based intent mapping is acceptable.
- It can support a small set of actions first: `run_iteration`, `get_status`, `analyze_trajs`, `accept_ckpt`, `stop`, `resume`.
- It does not call AReaL or EnvArena directly.

## 3. RSIController

Purpose:

- Execute structured actions.
- Persist run state.
- Manage async jobs.
- Support resume, retry, and stop.
- Ensure every important artifact is recorded.

Core responsibilities:

- Idempotent external calls.
- Run state transitions.
- Job ID tracking.
- Memory event writes.
- Error capture.

Core interface:

```python
class RSIController:
    def execute_action(self, action: dict) -> dict:
        ...

    def run_iteration(
        self,
        ckpt: str,
        *,
        task_set: str,
        harness_config: dict,
        method: str = "sft",
        train_config: dict | None = None,
        traj_filter: dict | None = None,
        eval_gate: dict | None = None,
    ) -> dict:
        ...

    def get_status(self, run_id: str) -> dict:
        ...

    def resume(self, run_id: str) -> dict:
        ...

    def stop(self, run_id: str, reason: str | None = None) -> dict:
        ...
```

Run steps:

1. `ensure_rollout_endpoint`
2. `register_model`
3. `submit_eval`
4. `collect_trajs`
5. `generate_dataset`
6. `train`
7. `update_endpoint`
8. `holdout_eval`
9. `record_decision_pending`

## 4. Run State

Purpose:

- Be the source of truth for current workflow progress.
- Allow recovery after process interruption.
- Prevent duplicated external submissions.

Suggested schema:

```json
{
  "run_id": "rsi_run_001",
  "iteration": 1,
  "status": "running",
  "current_step": "collect_trajs",
  "ckpt": "ckpt_000",
  "endpoint": {},
  "model_ref": {},
  "eval_job": {},
  "train_run": {},
  "holdout_job": {},
  "completed_steps": [],
  "pending_steps": [],
  "error": null
}
```

Storage:

```text
runs/<run_id>/state.json
runs/<run_id>/ledger.jsonl
runs/<run_id>/artifacts/
```

## 5. AReaLService

Purpose:

- Host model inference endpoints.
- Run SFT or OPD training.
- Update inference endpoints after new checkpoints.

Core interface:

```python
class AReaLService:
    def ensure_rollout_endpoint(self, ckpt: str, config: dict | None = None) -> dict:
        ...

    def update_rollout_endpoint(self, endpoint: dict, ckpt: str) -> dict:
        ...

    def train(
        self,
        base_ckpt: str,
        dataset: dict,
        method: str,
        config: dict,
        *,
        envarena: object | None = None,
        model_ref: dict | None = None,
        harness_config: dict | None = None,
    ) -> dict:
        ...
```

MVP:

- Implement mock service first.
- Real SFT integration comes before OPD.

## 6. EnvArenaClient

Purpose:

- Register AReaL inference endpoint.
- Submit evaluation and rollout jobs.
- Poll progress.
- Fetch trajectories.

Core interface:

```python
class EnvArenaClient:
    def register_model(self, endpoint: dict) -> dict:
        ...

    def update_model(self, model_ref: dict, endpoint: dict) -> dict:
        ...

    def submit_eval(
        self,
        model_ref: dict,
        task_set: str,
        harness_config: dict,
        metadata: dict | None = None,
    ) -> dict:
        ...

    def submit_rollout(
        self,
        model_ref: dict,
        tasks: list[dict],
        harness_config: dict,
        source: str,
        metadata: dict | None = None,
    ) -> dict:
        ...

    def get_trajs(self, job: dict, include_messages: bool = False) -> list[dict]:
        ...
```

MVP:

- Start with mock trajectories.
- Then implement the real endpoints from the current EnvArena integration in AReaL.
- Keep a trajectory normalization function so downstream modules do not depend on raw API details.

## 7. ResearchAgent

Purpose:

- Convert EnvArena trajectories into training datasets.
- Generate experience fields.
- Optionally validate experience through EnvArena.
- Record research attempts to Memory.
- Run as a worker agent, not as the user-facing conversation agent.

Core interface:

```python
class ResearchAgent:
    def generate_dataset(
        self,
        trajs: list[dict],
        memory_context: dict,
        current_ckpt: str,
        *,
        envarena: object | None = None,
        model_ref: dict | None = None,
        harness_config: dict | None = None,
        traj_filter: dict | None = None,
        config: dict | None = None,
    ) -> dict:
        ...

    def choose_ckpt(self, train_runs: list[dict], memory_context: dict) -> str:
        ...
```

MVP:

- Implement deterministic dataset construction first.
- Store `experience` as a dataset example field.
- Preserve `source_traj_ids`.
- Log filtering decisions.

Dataset example:

```json
{
  "task": {},
  "experience": "Use failing tests as localization evidence before patching.",
  "source_traj_ids": ["traj_001", "traj_002"],
  "metadata": {
    "generation_method": "rule_based_mvp"
  }
}
```

## 8. Memory

Purpose:

- Provide durable context for RSIAgent and ResearchAgent.
- Record every important run event.
- Support debugging and checkpoint selection.

Core interface:

```python
class Memory:
    def record(self, title: str, content: str, metadata: dict | None = None) -> str:
        ...

    def get_context(
        self,
        ckpt: str | None = None,
        run_id: str | None = None,
        limit: int = 100,
    ) -> dict:
        ...

    def append_ledger(self, event: dict) -> None:
        ...
```

Storage:

- `ledger.jsonl`: structured events.
- `insights.md`: human-readable accumulated lessons.
- `artifacts/`: dataset, train config, metrics, eval reports.

## 9. EvalFirewall

Purpose:

- Convert raw eval results into safe metrics and decisions.
- Hide private eval details when needed.
- Produce accept/reject recommendations.

Core interface:

```python
class EvalFirewall:
    def summarize(self, eval_result: dict, *, policy: dict | None = None) -> dict:
        ...

    def decide(self, baseline: dict, candidate: dict, gate: dict) -> dict:
        ...
```

MVP:

- Use simple metric deltas.
- Keep this optional until the basic SFT loop works.

## 10. CLI

Purpose:

- Run mock and real iterations.
- Inspect status.
- Resume or stop runs.

Initial commands:

```bash
areal-rsi run --ckpt ckpt_000 --task-set swe_verified --method sft
areal-rsi status <run_id>
areal-rsi resume <run_id>
areal-rsi stop <run_id>
areal-rsi chat
```

## 11. Testing Strategy

Unit tests:

- Memory ledger append/read.
- Run state save/load.
- Controller mock successful iteration.
- Controller records failures.
- Agent maps simple user messages into actions.

Integration tests:

- EnvArena eval submission and trajectory fetch.
- AReaL endpoint registration.
- AReaL SFT train run.

Integration tests should be opt-in because real EnvArena/AReaL jobs may require external services and GPU resources.

## 12. MVP Dependency Direction

```text
schemas
  -> memory
  -> mock services
  -> controller
  -> agent
  -> cli
```

Real integrations should depend on the service interfaces, not on controller internals.
