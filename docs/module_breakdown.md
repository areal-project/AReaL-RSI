# AReaL-RSI Module Breakdown

## 1. Boundary Overview

```text
Human
  <-> RSIAgent
        -> RSIController
              -> AReaLService
              -> EnvClient
              -> ResearchAgent
              -> Memory
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

- Concrete scaffold is based on OpenAI SDK via `OpenAIRSIAgent`.
- It can support a small set of actions first: `run_iteration`, `get_status`, `analyze_trajs`, `accept_ckpt`, `stop`, `resume`.
- It does not call AReaL or EnvArena directly.
- It may be tested with fake OpenAI clients without installing the SDK.

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
  "iteration": 2,
  "status": "running",
  "current_step": "collect_trajs",
  "ckpt": "ckpt_002",
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
runs/<run_id>/audit.jsonl
runs/<run_id>/artifacts/iter_001/
runs/<run_id>/artifacts/iter_002/
```

`run_id` represents one RSI experiment. `iteration` represents the loop
round inside that experiment. `run_iteration(...)` still runs one round for
local tests and adapters; `run_loop(...)` calls the same round executor
multiple times and records `metadata.iteration_results`,
`metadata.accepted_ckpts`, and `metadata.final_ckpt`.

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

## 6. EnvClient and EnvArenaClient

Purpose:

- Register AReaL inference endpoint.
- Submit evaluation jobs.
- Poll progress.
- Fetch trajectories.

Core interface:

```python
class EnvClient(Protocol):
    def register_model(self, endpoint: dict) -> dict:
        ...

    def update_model(self, model_ref: dict, endpoint: dict) -> dict:
        ...

    def submit_eval(self, request: EvalRequest) -> dict:
        ...

    def get_eval_progress(self, job: dict) -> dict:
        ...

    def get_eval_result(self, job: dict) -> dict:
        ...

    def get_trajs(self, job: dict, include_messages: bool = False) -> list[dict]:
        ...
```

MVP:

- `MockEnvClient` implements the generic local workflow contract.
- `EnvArenaClient` implements the generic contract plus EnvArena-specific
  asset discovery, BenchmarkJob, and trajectory-detail APIs.
- Keep trajectory normalization so downstream modules do not depend on raw API details.

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

- Concrete scaffold is based on OpenAI SDK via `OpenAIResearchAgent`.
- Keep `MockResearchAgent` for deterministic local controller tests.
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

## 8. AuditLog And Memory

AuditLog purpose:

- Record every controller action with input, output, artifacts, status, and errors.
- Provide deterministic provenance for eval scores, datasets, train runs, and checkpoint decisions.
- Keep large payloads in artifacts and store paths in `audit.jsonl`.

AuditLog storage:

- `runs/<run_id>/audit.jsonl`: append-only machine-readable events.
- `runs/<run_id>/artifacts/`: full payloads referenced by audit events.

Memory purpose:

- Provide durable context for RSIAgent and ResearchAgent.
- Record research summaries, observations, decisions, and human feedback.
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

## 9. CLI

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

## 10. Testing Strategy

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

## 11. MVP Dependency Direction

```text
schemas
  -> memory
  -> mock services
  -> controller
  -> agent
  -> cli
```

Real integrations should depend on the service interfaces, not on controller internals.
